# ai-agent1/app/k8s_monitor.py
import logging
from datetime import datetime, timedelta, timezone, MINYEAR
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import os
import sys # Import sys for printing during init

# --- Global K8s Clients (initialized later) ---
k8s_core_v1 = None
k8s_apps_v1 = None
k8s_client_initialized = False # Flag to track initialization

# --- Initialization Function ---
def initialize_k8s_client():
    """Loads K8s config and initializes API clients."""
    global k8s_core_v1, k8s_apps_v1, k8s_client_initialized

    # Prevent re-initialization
    if k8s_client_initialized:
        logging.debug("[K8s Monitor] Client already initialized.")
        return True

    print("[DEBUG_PRINT] Attempting to initialize K8s client...", flush=True)
    sys.stdout.flush()
    logging.info("[K8s Monitor] Attempting to initialize Kubernetes client...")

    try:
        # Try loading incluster config first
        print("[DEBUG_PRINT] Trying in-cluster config...", flush=True)
        sys.stdout.flush()
        config.load_incluster_config()
        logging.info("[K8s Monitor] Loaded in-cluster Kubernetes config.")
        k8s_core_v1 = client.CoreV1Api()
        k8s_apps_v1 = client.AppsV1Api()
        k8s_client_initialized = True
        print("[DEBUG_PRINT] In-cluster config loaded successfully.", flush=True)
        sys.stdout.flush()
        logging.info("[K8s Monitor] Kubernetes client initialized successfully (in-cluster).")
        return True
    except config.ConfigException as e1:
        print(f"[DEBUG_PRINT] In-cluster config failed ({e1}), trying kubeconfig...", flush=True)
        sys.stdout.flush()
        logging.warning(f"[K8s Monitor] In-cluster config failed ({e1}), trying kubeconfig...")
        try:
            # Fallback to kubeconfig
            config.load_kube_config()
            logging.info("[K8s Monitor] Loaded local Kubernetes config (kubeconfig).")
            k8s_core_v1 = client.CoreV1Api()
            k8s_apps_v1 = client.AppsV1Api()
            k8s_client_initialized = True
            print("[DEBUG_PRINT] Kubeconfig loaded successfully.", flush=True)
            sys.stdout.flush()
            logging.info("[K8s Monitor] Kubernetes client initialized successfully (kubeconfig).")
            return True
        except config.ConfigException as e2:
            print(f"[DEBUG_PRINT] Kubeconfig failed ({e2}). K8s monitoring unavailable.", flush=True)
            sys.stdout.flush()
            logging.error(f"[K8s Monitor] Could not configure Kubernetes client (in-cluster failed: {e1}, kubeconfig failed: {e2}). K8s monitoring features will be unavailable.")
            return False
        except Exception as e_kube: # Catch other potential errors during kubeconfig loading
            print(f"[DEBUG_PRINT] Unexpected error loading kubeconfig: {e_kube}", flush=True)
            sys.stdout.flush()
            logging.error(f"[K8s Monitor] Unexpected error loading kubeconfig: {e_kube}", exc_info=True)
            return False
    except Exception as e_global: # Catch other potential errors during incluster loading
        print(f"[DEBUG_PRINT] Unexpected error loading in-cluster config: {e_global}", flush=True)
        sys.stdout.flush()
        logging.error(f"[K8s Monitor] Unexpected error loading in-cluster config: {e_global}", exc_info=True)
        return False


# --- Kubernetes Info Functions (Add client checks) ---

def get_pod_info(namespace, pod_name):
    if not k8s_client_initialized or not k8s_core_v1:
        logging.warning("[K8s Monitor] K8s client not initialized. Cannot get pod info.")
        return None
    try:
        pod = k8s_core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        info = {
            "name": pod.metadata.name, "namespace": pod.metadata.namespace, "status": pod.status.phase,
            "node_name": pod.spec.node_name,
            "start_time": pod.status.start_time.isoformat() if pod.status.start_time else "N/A",
            "restarts": sum(cs.restart_count for cs in pod.status.container_statuses) if pod.status.container_statuses else 0,
            "conditions": {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message} for cond in pod.status.conditions} if pod.status.conditions else {},
            "container_statuses": {}
        }
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                state_info = "N/A";
                if cs.state:
                    if cs.state.running: state_info = "Running"
                    elif cs.state.waiting: state_info = f"Waiting ({cs.state.waiting.reason})"
                    elif cs.state.terminated: state_info = f"Terminated ({cs.state.terminated.reason}, ExitCode: {cs.state.terminated.exit_code})"
                info["container_statuses"][cs.name] = {"ready": cs.ready, "restart_count": cs.restart_count, "state": state_info}
        return info
    except ApiException as e:
        if e.status != 404:
             logging.warning(f"[K8s Monitor] Could not get pod info for {namespace}/{pod_name}: {e.status} {e.reason}")
        return None
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error getting pod info for {namespace}/{pod_name}: {e}", exc_info=True)
        return None

def get_node_info(node_name):
    if not k8s_client_initialized or not k8s_core_v1:
        logging.warning("[K8s Monitor] K8s client not initialized. Cannot get node info.")
        return None
    if not node_name: return None
    try:
        node = k8s_core_v1.read_node(name=node_name)
        conditions = {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message} for cond in node.status.conditions} if node.status.conditions else {}
        info = { "name": node.metadata.name, "conditions": conditions,
                 "allocatable_cpu": node.status.allocatable.get('cpu', 'N/A'),
                 "allocatable_memory": node.status.allocatable.get('memory', 'N/A'),
                 "kubelet_version": node.status.node_info.kubelet_version }
        return info
    except ApiException as e:
        if e.status != 404:
            logging.warning(f"[K8s Monitor] Could not get node info for {node_name}: {e.status} {e.reason}")
        return None
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error getting node info for {node_name}: {e}", exc_info=True)
        return None

def get_pod_events(namespace, pod_name, since_minutes=15):
    if not k8s_client_initialized or not k8s_core_v1:
        logging.warning("[K8s Monitor] K8s client not initialized. Cannot get pod events.")
        return []
    try:
        since_time = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        field_selector = f"involvedObject.kind=Pod,involvedObject.name={pod_name},involvedObject.namespace={namespace}"
        events = k8s_core_v1.list_namespaced_event(namespace=namespace, field_selector=field_selector, limit=20)
        recent_events = []
        if events and events.items:
                sorted_events = sorted(
                    events.items,
                    key=lambda e: e.last_timestamp or e.metadata.creation_timestamp or datetime(MINYEAR, 1, 1, tzinfo=timezone.utc),
                    reverse=True
                )
                for event in sorted_events:
                    event_time = event.last_timestamp or event.metadata.creation_timestamp
                    if event_time and event_time >= since_time:
                        recent_events.append({
                            "time": event_time.isoformat(), "type": event.type, "reason": event.reason,
                            "message": event.message, "count": event.count
                        })
                    if len(recent_events) >= 10 or (event_time and event_time < since_time): break
        return recent_events
    except ApiException as e:
        if e.status != 403:
            logging.warning(f"[K8s Monitor] Could not list events for pod {namespace}/{pod_name}: {e.status} {e.reason}")
        return []
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error listing events for pod {namespace}/{pod_name}: {e}", exc_info=True)
        return []

def format_k8s_context(pod_info, node_info, pod_events):
    context_str = "\n--- Ngữ cảnh Kubernetes ---\n"
    if not k8s_client_initialized:
        return "--- Không có ngữ cảnh Kubernetes (Client K8s chưa khởi tạo) ---\n"
    if not pod_info and not node_info and not pod_events:
         return "--- Không có ngữ cảnh Kubernetes (Không tìm thấy thông tin Pod/Node/Event) ---\n"

    if pod_info:
        context_str += f"Pod: {pod_info['namespace']}/{pod_info['name']}\n"
        context_str += f"  Trạng thái: {pod_info['status']}\n"
        context_str += f"  Node: {pod_info.get('node_name', 'N/A')}\n"
        context_str += f"  Số lần khởi động lại: {pod_info.get('restarts', 0)}\n"
        if pod_info.get('container_statuses'):
                context_str += "  Trạng thái Container:\n"
                for name, status in pod_info['container_statuses'].items(): context_str += f"    - {name}: {status['state']} (Ready: {status['ready']}, Restarts: {status['restart_count']})\n"
        if pod_info.get('conditions'):
                problematic_conditions = [ f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})" for ctype, cinfo in pod_info['conditions'].items() if cinfo.get('status') != 'True' ]
                if problematic_conditions: context_str += f"  Điều kiện Pod bất thường: {', '.join(problematic_conditions)}\n"
    else:
        context_str += "Pod Info: Not Available\n"

    if node_info:
        context_str += f"Node: {node_info['name']}\n"
        if node_info.get('conditions'):
                problematic_conditions = [ f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})" for ctype, cinfo in node_info['conditions'].items() if (ctype == 'Ready' and cinfo.get('status') != 'True') or (ctype != 'Ready' and cinfo.get('status') != 'False') ]
                if problematic_conditions: context_str += f"  Điều kiện Node bất thường: {', '.join(problematic_conditions)}\n"

    if pod_events:
        context_str += "Sự kiện Pod gần đây (tối đa 10):\n"
        for event in pod_events:
            message_preview = event['message'][:150] + ('...' if len(event['message']) > 150 else '')
            context_str += f"  - [{event['time']}] {event['type']} {event['reason']} (x{event.get('count',1)}): {message_preview}\n"

    context_str += "--- Kết thúc ngữ cảnh ---\n"
    return context_str

def scan_kubernetes_for_issues(namespaces_to_scan, restart_threshold, loki_detail_log_range_minutes=30):
    if not k8s_client_initialized or not k8s_core_v1:
        logging.warning("[K8s Monitor] K8s client not initialized. Skipping K8s scan.")
        return {}

    problematic_pods = {}
    logging.info(f"[K8s Monitor] Scanning {len(namespaces_to_scan)} Kubernetes namespaces for problematic pods...")
    for ns in namespaces_to_scan:
        try:
            pods = k8s_core_v1.list_namespaced_pod(namespace=ns, watch=False, timeout_seconds=60)
            for pod in pods.items:
                pod_key = f"{ns}/{pod.metadata.name}"; issue_found = False; reason = ""
                if pod.status.phase in ["Failed", "Unknown"]: issue_found = True; reason = f"Trạng thái Pod là {pod.status.phase}"
                elif pod.status.phase == "Pending" and pod.status.conditions:
                        scheduled_condition = next((c for c in pod.status.conditions if c.type == "PodScheduled"), None)
                        if scheduled_condition and scheduled_condition.status == "False" and scheduled_condition.reason == "Unschedulable": issue_found = True; reason = f"Pod không thể lên lịch (Unschedulable)"
                if not issue_found and pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        if cs.restart_count >= restart_threshold:
                            issue_found = True; reason = f"Container '{cs.name}' restart {cs.restart_count} lần (>= ngưỡng {restart_threshold})"; break
                        if cs.state and cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CreateContainerError"]:
                            issue_found = True; reason = f"Container '{cs.name}' đang ở trạng thái Waiting với lý do '{cs.state.waiting.reason}'"; break
                        if cs.state and cs.state.terminated and cs.state.terminated.reason in ["OOMKilled", "Error", "ContainerCannotRun"]:
                            is_recent_termination = False
                            if cs.state.terminated.finished_at:
                                try:
                                    finished_at_aware = cs.state.terminated.finished_at
                                    if finished_at_aware.tzinfo is None: finished_at_aware = finished_at_aware.replace(tzinfo=timezone.utc)
                                    is_recent_termination = (datetime.now(timezone.utc) - finished_at_aware) < timedelta(minutes=loki_detail_log_range_minutes)
                                except Exception as time_err: logging.warning(f"[K8s Monitor] Could not parse/compare finished_at for {pod_key}/{cs.name}: {time_err}")
                            if pod.spec.restart_policy != "Always" or is_recent_termination:
                                issue_found = True; reason = f"Container '{cs.name}' bị Terminated với lý do '{cs.state.terminated.reason}'"; break
                if issue_found:
                    logging.warning(f"[K8s Monitor] Phát hiện pod có vấn đề tiềm ẩn: {pod_key}. Lý do: {reason}")
                    if pod_key not in problematic_pods: problematic_pods[pod_key] = { "namespace": ns, "pod_name": pod.metadata.name, "reason": f"K8s: {reason}" }
        except ApiException as e:
            logging.error(f"[K8s Monitor] API Error scanning namespace {ns}: {e.status} {e.reason}")
        except Exception as e:
            logging.error(f"[K8s Monitor] Unexpected error scanning namespace {ns}: {e}", exc_info=True)
    logging.info(f"[K8s Monitor] Finished K8s scan. Found {len(problematic_pods)} potentially problematic pods.")
    return problematic_pods

def get_active_namespaces(excluded_namespaces):
    if not k8s_client_initialized or not k8s_core_v1:
        logging.warning("[K8s Monitor] K8s client not initialized. Cannot get active namespaces.")
        return []
    active_namespaces = []
    try:
        all_namespaces = k8s_core_v1.list_namespace(watch=False, timeout_seconds=60)
        for ns in all_namespaces.items:
            if ns.status.phase == "Active" and ns.metadata.name not in excluded_namespaces:
                active_namespaces.append(ns.metadata.name)
        logging.info(f"[K8s Monitor] Found {len(active_namespaces)} active and non-excluded namespaces in cluster.")
    except ApiException as e:
        logging.error(f"[K8s Monitor] API Error listing namespaces: {e.status} {e.reason}. Check RBAC permissions.")
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error listing namespaces: {e}", exc_info=True)
    return active_namespaces


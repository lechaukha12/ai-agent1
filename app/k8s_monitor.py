import logging
from datetime import datetime, timedelta, timezone, MINYEAR
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import os
import sys

k8s_core_v1 = None
k8s_apps_v1 = None
k8s_client_initialized = False

def initialize_k8s_client():
    global k8s_core_v1, k8s_apps_v1, k8s_client_initialized
    if k8s_client_initialized:
        logging.debug("[K8s Monitor] Client already initialized.")
        return True
    print("[DEBUG_PRINT] Attempting to initialize K8s client...", flush=True)
    logging.info("[K8s Monitor] Attempting to initialize Kubernetes client...")
    try:
        print("[DEBUG_PRINT] Trying in-cluster config...", flush=True)
        config.load_incluster_config()
        logging.info("[K8s Monitor] Loaded in-cluster Kubernetes config.")
        k8s_core_v1 = client.CoreV1Api()
        k8s_client_initialized = True
        print("[DEBUG_PRINT] In-cluster config loaded successfully.", flush=True)
        logging.info("[K8s Monitor] Kubernetes client initialized successfully (in-cluster).")
        return True
    except config.ConfigException as e1:
        print(f"[DEBUG_PRINT] In-cluster config failed ({e1}), trying kubeconfig...", flush=True)
        logging.warning(f"[K8s Monitor] In-cluster config failed ({e1}), trying kubeconfig...")
        try:
            config.load_kube_config()
            logging.info("[K8s Monitor] Loaded local Kubernetes config (kubeconfig).")
            k8s_core_v1 = client.CoreV1Api()
            k8s_client_initialized = True
            print("[DEBUG_PRINT] Kubeconfig loaded successfully.", flush=True)
            logging.info("[K8s Monitor] Kubernetes client initialized successfully (kubeconfig).")
            return True
        except config.ConfigException as e2:
            print(f"[DEBUG_PRINT] Kubeconfig failed ({e2}). K8s monitoring unavailable.", flush=True)
            logging.error(f"[K8s Monitor] Could not configure Kubernetes client (in-cluster failed: {e1}, kubeconfig failed: {e2}). K8s monitoring features will be unavailable.")
            return False
        except Exception as e_kube:
            print(f"[DEBUG_PRINT] Unexpected error loading kubeconfig: {e_kube}", flush=True)
            logging.error(f"[K8s Monitor] Unexpected error loading kubeconfig: {e_kube}", exc_info=True)
            return False
    except Exception as e_global:
        print(f"[DEBUG_PRINT] Unexpected error loading in-cluster config: {e_global}", flush=True)
        logging.error(f"[K8s Monitor] Unexpected error loading in-cluster config: {e_global}", exc_info=True)
        return False

def get_pod_info(namespace, pod_name):
    if not k8s_client_initialized or not k8s_core_v1:
        logging.warning("[K8s Monitor] K8s client not initialized. Cannot get pod info.")
        return None
    try:
        pod = k8s_core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        info = {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "status": pod.status.phase,
            "node_name": pod.spec.node_name,
            "start_time": pod.status.start_time.isoformat() if pod.status.start_time else "N/A",
            "restarts": sum(cs.restart_count for cs in pod.status.container_statuses) if pod.status.container_statuses else 0,
            "conditions": {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message}
                           for cond in pod.status.conditions} if pod.status.conditions else {},
            "container_statuses": {}
        }
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                state_info = "N/A"
                last_state_info = "N/A"
                if cs.state:
                    if cs.state.running: state_info = "Running"
                    elif cs.state.waiting: state_info = f"Waiting ({cs.state.waiting.reason or 'N/A'})"
                    elif cs.state.terminated: state_info = f"Terminated ({cs.state.terminated.reason or 'N/A'}, ExitCode: {cs.state.terminated.exit_code})"
                if cs.last_state:
                    if cs.last_state.running: last_state_info = "Last: Running"
                    elif cs.last_state.waiting: last_state_info = f"Last: Waiting ({cs.last_state.waiting.reason or 'N/A'})"
                    elif cs.last_state.terminated: last_state_info = f"Last: Terminated ({cs.last_state.terminated.reason or 'N/A'}, ExitCode: {cs.last_state.terminated.exit_code})"

                info["container_statuses"][cs.name] = {
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "state": state_info,
                    "last_state": last_state_info
                }
        return info
    except ApiException as e:
        if e.status != 404:
             logging.warning(f"[K8s Monitor] API error getting pod info for {namespace}/{pod_name}: {e.status} {e.reason}")
        return None
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error getting pod info for {namespace}/{pod_name}: {e}", exc_info=True)
        return None

def get_node_info(node_name):
    if not k8s_client_initialized or not k8s_core_v1:
        logging.warning("[K8s Monitor] K8s client not initialized. Cannot get node info.")
        return None
    if not node_name:
        return None
    try:
        node = k8s_core_v1.read_node(name=node_name)
        conditions = {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message}
                      for cond in node.status.conditions} if node.status.conditions else {}
        info = {
            "name": node.metadata.name,
            "conditions": conditions,
            "allocatable_cpu": node.status.allocatable.get('cpu', 'N/A'),
            "allocatable_memory": node.status.allocatable.get('memory', 'N/A'),
            "capacity_cpu": node.status.capacity.get('cpu', 'N/A'),
            "capacity_memory": node.status.capacity.get('memory', 'N/A'),
            "kubelet_version": node.status.node_info.kubelet_version,
            "os_image": node.status.node_info.os_image,
            "kernel_version": node.status.node_info.kernel_version
            }
        return info
    except ApiException as e:
        if e.status != 404:
            logging.warning(f"[K8s Monitor] API error getting node info for {node_name}: {e.status} {e.reason}")
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
        events = k8s_core_v1.list_namespaced_event(namespace=namespace, field_selector=field_selector, limit=50)

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
                            "time": event_time.isoformat(),
                            "type": event.type,
                            "reason": event.reason,
                            "message": event.message,
                            "count": event.count,
                            "source_component": event.source.component if event.source else 'N/A'
                        })
                    if len(recent_events) >= 15:
                        break
                    if event_time and event_time < since_time:
                        break
        return recent_events
    except ApiException as e:
        if e.status != 403:
            logging.warning(f"[K8s Monitor] API error listing events for pod {namespace}/{pod_name}: {e.status} {e.reason}")
        return []
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error listing events for pod {namespace}/{pod_name}: {e}", exc_info=True)
        return []

def format_k8s_context(pod_info, node_info, pod_events):
    context_lines = ["--- Kubernetes Context ---"]
    if not k8s_client_initialized:
        context_lines.append("K8s Client not initialized.")
        return "\n".join(context_lines)
    if not pod_info and not node_info and not pod_events:
         context_lines.append("No Pod/Node/Event information available.")
         return "\n".join(context_lines)
    if pod_info:
        context_lines.append(f"Pod: {pod_info.get('namespace', 'N/A')}/{pod_info.get('name', 'N/A')}")
        context_lines.append(f"  Status: {pod_info.get('status', 'N/A')}")
        context_lines.append(f"  Node: {pod_info.get('node_name', 'N/A')}")
        context_lines.append(f"  Restarts: {pod_info.get('restarts', 0)}")
        context_lines.append(f"  StartTime: {pod_info.get('start_time', 'N/A')}")
        if pod_info.get('container_statuses'):
                context_lines.append("  Container Statuses:")
                for name, status in pod_info['container_statuses'].items():
                    context_lines.append(f"    - {name}: {status.get('state', 'N/A')} (Ready: {status.get('ready', 'N/A')}, Restarts: {status.get('restart_count', 0)}, LastState: {status.get('last_state', 'N/A')})")
        if pod_info.get('conditions'):
                problematic_conditions = [
                    f"{ctype}({cinfo.get('status','N/A')}-{cinfo.get('reason','N/A')})"
                    for ctype, cinfo in pod_info['conditions'].items()
                    if (ctype == "Ready" and cinfo.get('status') != 'True') or \
                       (ctype == "ContainersReady" and cinfo.get('status') != 'True') or \
                       (ctype == "Initialized" and cinfo.get('status') != 'True') or \
                       (ctype == "PodScheduled" and cinfo.get('status') != 'True')
                ]
                if problematic_conditions:
                    context_lines.append(f"  Problematic Conditions: {', '.join(problematic_conditions)}")
    else:
        context_lines.append("Pod Info: Not Available")
    if node_info:
        context_lines.append(f"Node: {node_info.get('name', 'N/A')}")
        context_lines.append(f"  Kubelet: {node_info.get('kubelet_version', 'N/A')}")
        context_lines.append(f"  OS: {node_info.get('os_image', 'N/A')}")
        context_lines.append(f"  Kernel: {node_info.get('kernel_version', 'N/A')}")
        context_lines.append(f"  Allocatable: CPU={node_info.get('allocatable_cpu', 'N/A')}, Mem={node_info.get('allocatable_memory', 'N/A')}")
        if node_info.get('conditions'):
                problematic_conditions = [
                    f"{ctype}({cinfo.get('status','N/A')}-{cinfo.get('reason','N/A')})"
                    for ctype, cinfo in node_info['conditions'].items()
                    if (ctype == 'Ready' and cinfo.get('status') != 'True') or \
                       (ctype != 'Ready' and cinfo.get('status') == 'True')
                ]
                if problematic_conditions:
                    context_lines.append(f"  Problematic Node Conditions: {', '.join(problematic_conditions)}")
    if pod_events:
        context_lines.append("Recent Pod Events (max 15):")
        for event in pod_events:
            message_preview = event.get('message', '')[:150] + ('...' if len(event.get('message', '')) > 150 else '')
            context_lines.append(f"  - [{event.get('time', 'N/A')}] {event.get('type', 'N/A')} {event.get('reason', 'N/A')} (x{event.get('count',1)}, {event.get('source_component', 'N/A')}): {message_preview}")
    context_lines.append("--- End Context ---")
    return "\n".join(context_lines)

def scan_kubernetes_for_issues(namespaces_to_scan, restart_threshold, loki_detail_log_range_minutes=30):
    if not k8s_client_initialized or not k8s_core_v1:
        logging.warning("[K8s Monitor] K8s client not initialized. Skipping K8s scan.")
        return {}
    problematic_pods = {}
    logging.info(f"[K8s Monitor] Scanning {len(namespaces_to_scan)} Kubernetes namespaces for problematic pods...")
    recent_termination_threshold = datetime.now(timezone.utc) - timedelta(minutes=loki_detail_log_range_minutes)
    for ns in namespaces_to_scan:
        try:
            pods = k8s_core_v1.list_namespaced_pod(namespace=ns, watch=False, timeout_seconds=60)
            for pod in pods.items:
                pod_key = f"{ns}/{pod.metadata.name}"
                issue_found = False
                reason = ""
                if pod.status.phase in ["Failed", "Unknown"]:
                    issue_found = True
                    reason = f"Pod phase is {pod.status.phase}"
                elif pod.status.phase == "Pending" and pod.status.conditions:
                        scheduled_condition = next((c for c in pod.status.conditions if c.type == "PodScheduled"), None)
                        if scheduled_condition and scheduled_condition.status == "False" and scheduled_condition.reason == "Unschedulable":
                            issue_found = True
                            reason = f"Pod is Unschedulable"
                if not issue_found and pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        if cs.restart_count >= restart_threshold:
                            issue_found = True
                            reason = f"Container '{cs.name}' restarted {cs.restart_count} times (>= threshold {restart_threshold})"
                            break
                        if cs.state and cs.state.waiting and cs.state.waiting.reason in [
                            "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                            "CreateContainerConfigError", "CreateContainerError", "InvalidImageName"
                        ]:
                            issue_found = True
                            reason = f"Container '{cs.name}' in Waiting state ({cs.state.waiting.reason})"
                            break
                        if cs.state and cs.state.terminated and cs.state.terminated.reason in [
                            "OOMKilled", "Error", "ContainerCannotRun", "DeadlineExceeded"
                        ]:
                            is_recent_termination = False
                            finished_at_str = cs.state.terminated.finished_at
                            if finished_at_str:
                                try:
                                    finished_at_aware = finished_at_str
                                    if finished_at_aware >= recent_termination_threshold:
                                         is_recent_termination = True
                                except Exception as time_err:
                                    logging.warning(f"[K8s Monitor] Could not parse/compare finished_at for {pod_key}/{cs.name}: {time_err}")
                            if is_recent_termination or pod.spec.restart_policy != "Always":
                                issue_found = True
                                reason = f"Container '{cs.name}' Terminated ({cs.state.terminated.reason}, recent: {is_recent_termination})"
                                break
                if issue_found:
                    logging.warning(f"[K8s Monitor] Found potentially problematic pod: {pod_key}. Reason: {reason}")
                    if pod_key not in problematic_pods:
                        problematic_pods[pod_key] = {
                            "namespace": ns,
                            "pod_name": pod.metadata.name,
                            "reason": f"K8s: {reason}"
                        }
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

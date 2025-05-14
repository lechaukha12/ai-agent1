# ai-agent1/app/k8s_monitor.py
import logging
from datetime import datetime, timedelta, timezone, MINYEAR
# Sử dụng thư viện asyncio của kubernetes
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.exceptions import ApiException
import os
import sys
import json
import asyncio

# Kubernetes API clients (initialized later)
k8s_core_v1 = None
k8s_apps_v1 = None
k8s_batch_v1 = None
k8s_version_api = None
k8s_client_initialized = False
_k8s_init_lock = asyncio.Lock()

# Chuyển thành hàm async
async def initialize_k8s_client():
    """Initializes Kubernetes API clients asynchronously."""
    global k8s_core_v1, k8s_apps_v1, k8s_batch_v1, k8s_version_api, k8s_client_initialized
    async with _k8s_init_lock:
        if k8s_client_initialized:
            logging.debug("[K8s Monitor] Async client already initialized.")
            return True
        logging.info("[K8s Monitor] Attempting to initialize Async Kubernetes client...")
        try:
            # --- SỬA LỖI: Bỏ await ở đây ---
            config.load_incluster_config()
            # ---------------------------
            logging.info("[K8s Monitor] Loaded in-cluster Kubernetes config (sync).")
            api_client = client.ApiClient()
            k8s_core_v1 = client.CoreV1Api(api_client)
            k8s_apps_v1 = client.AppsV1Api(api_client)
            k8s_batch_v1 = client.BatchV1Api(api_client)
            k8s_version_api = client.VersionApi(api_client)
            k8s_client_initialized = True
            logging.info("[K8s Monitor] Async Kubernetes client initialized successfully (in-cluster).")
            return True
        except config.ConfigException as e1:
            logging.warning(f"[K8s Monitor] In-cluster config failed ({e1}), trying kubeconfig (async)...")
            try:
                # --- SỬA LỖI: Bỏ await ở đây ---
                config.load_kube_config()
                # ---------------------------
                logging.info("[K8s Monitor] Loaded local Kubernetes config (kubeconfig - sync).")
                api_client = client.ApiClient()
                k8s_core_v1 = client.CoreV1Api(api_client)
                k8s_apps_v1 = client.AppsV1Api(api_client)
                k8s_batch_v1 = client.BatchV1Api(api_client)
                k8s_version_api = client.VersionApi(api_client)
                k8s_client_initialized = True
                logging.info("[K8s Monitor] Async Kubernetes client initialized successfully (kubeconfig).")
                return True
            except config.ConfigException as e2:
                logging.error(f"[K8s Monitor] Could not configure Kubernetes client (in-cluster failed: {e1}, kubeconfig failed: {e2}). K8s monitoring features will be unavailable.")
                return False
            except Exception as e_kube:
                logging.error(f"[K8s Monitor] Unexpected error loading kubeconfig (async): {e_kube}", exc_info=True)
                return False
        except Exception as e_global:
            # Bắt lỗi cụ thể hơn nếu có thể, ví dụ lỗi permission khi đọc token
            logging.error(f"[K8s Monitor] Unexpected error during K8s client initialization (async): {e_global}", exc_info=True)
            return False

# --- Các hàm async khác giữ nguyên ---
async def get_cluster_summary():
    """Retrieves basic cluster information asynchronously."""
    if not k8s_client_initialized:
        if not await initialize_k8s_client():
            logging.warning("[K8s Monitor] K8s client not initialized. Cannot get cluster summary.")
            return {"k8s_version": "N/A", "node_count": 0}

    summary = {"k8s_version": "N/A", "node_count": 0}
    # ... (phần còn lại của hàm giữ nguyên) ...
    try:
        if k8s_version_api:
            version_info = await k8s_version_api.get_code()
            summary["k8s_version"] = version_info.git_version or "N/A"
        else:
            logging.warning("[K8s Monitor] Version API client not available for cluster summary.")
    except ApiException as e:
        logging.error(f"[K8s Monitor] API error getting K8s version: {e.status} {e.reason}")
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error getting K8s version: {e}", exc_info=True)

    try:
        if k8s_core_v1:
            nodes = await k8s_core_v1.list_node(watch=False, _request_timeout=30)
            summary["node_count"] = len(nodes.items) if nodes and nodes.items else 0
        else:
             logging.warning("[K8s Monitor] Core V1 API client not available for node count.")
    except ApiException as e:
        logging.error(f"[K8s Monitor] API error listing nodes for count: {e.status} {e.reason}")
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error listing nodes for count: {e}", exc_info=True)

    logging.debug(f"[K8s Monitor] Cluster summary retrieved: {summary}")
    return summary


async def get_pod_info(namespace, pod_name):
    """Lấy thông tin chi tiết của Pod asynchronously."""
    if not k8s_client_initialized or not k8s_core_v1:
        if not await initialize_k8s_client():
            logging.warning("[K8s Monitor] K8s client not initialized. Cannot get pod info.")
            return None
    try:
        pod = await k8s_core_v1.read_namespaced_pod(name=pod_name, namespace=namespace, _request_timeout=15)
        # ... (phần còn lại của hàm giữ nguyên) ...
        info = {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "status": pod.status.phase,
            "reason": pod.status.reason,
            "message": pod.status.message,
            "node_name": pod.spec.node_name,
            "start_time": pod.status.start_time.isoformat() if pod.status.start_time else "N/A",
            "restarts": sum(cs.restart_count for cs in pod.status.container_statuses) if pod.status.container_statuses else 0,
            "conditions": {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message}
                           for cond in pod.status.conditions} if pod.status.conditions else {},
            "container_statuses": {},
            "owner_references": [
                {"kind": owner.kind, "name": owner.name, "uid": owner.uid}
                for owner in pod.metadata.owner_references
            ] if pod.metadata.owner_references else []
        }

        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                state_info = "N/A"
                last_state_info = "N/A"
                terminated_info = {}
                last_terminated_info = {}

                if cs.state:
                    if cs.state.running: state_info = "Running"
                    elif cs.state.waiting: state_info = f"Waiting ({cs.state.waiting.reason or 'N/A'})"
                    elif cs.state.terminated:
                        state_info = f"Terminated ({cs.state.terminated.reason or 'N/A'}, ExitCode: {cs.state.terminated.exit_code})"
                        terminated_info = {
                             'reason': cs.state.terminated.reason, 'exit_code': cs.state.terminated.exit_code,
                             'started_at': cs.state.terminated.started_at.isoformat() if cs.state.terminated.started_at else None,
                             'finished_at': cs.state.terminated.finished_at.isoformat() if cs.state.terminated.finished_at else None,
                        }
                if cs.last_state:
                    if cs.last_state.running: last_state_info = "Last: Running"
                    elif cs.last_state.waiting: last_state_info = f"Last: Waiting ({cs.last_state.waiting.reason or 'N/A'})"
                    elif cs.last_state.terminated:
                         last_state_info = f"Last: Terminated ({cs.last_state.terminated.reason or 'N/A'}, ExitCode: {cs.last_state.terminated.exit_code})"
                         last_terminated_info = {
                             'reason': cs.last_state.terminated.reason, 'exit_code': cs.last_state.terminated.exit_code,
                             'started_at': cs.last_state.terminated.started_at.isoformat() if cs.last_state.terminated.started_at else None,
                             'finished_at': cs.last_state.terminated.finished_at.isoformat() if cs.last_state.terminated.finished_at else None,
                         }
                info["container_statuses"][cs.name] = {
                    "ready": cs.ready, "restart_count": cs.restart_count, "state": state_info,
                    "terminated_details": terminated_info, "last_state": last_state_info,
                    "last_terminated_details": last_terminated_info
                }
        return info
    except ApiException as e:
        if e.status != 404:
             logging.warning(f"[K8s Monitor] API error getting pod info for {namespace}/{pod_name}: {e.status} {e.reason}")
        return None
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error getting pod info for {namespace}/{pod_name}: {e}", exc_info=True)
        return None

async def get_node_info(node_name):
    """Lấy thông tin Node asynchronously."""
    # ... (giữ nguyên code) ...
    if not k8s_client_initialized or not k8s_core_v1:
        if not await initialize_k8s_client():
            logging.warning("[K8s Monitor] K8s client not initialized. Cannot get node info.")
            return None
    if not node_name:
        return None
    try:
        node = await k8s_core_v1.read_node(name=node_name, _request_timeout=15)
        conditions = {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message}
                      for cond in node.status.conditions} if node.status.conditions else {}
        info = {
            "name": node.metadata.name, "conditions": conditions,
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


async def get_pod_events(namespace, pod_name, since_minutes=60, limit=50):
    """Lấy các event gần đây của Pod asynchronously."""
    # ... (giữ nguyên code) ...
    if not k8s_client_initialized or not k8s_core_v1:
        if not await initialize_k8s_client():
            logging.warning("[K8s Monitor] K8s client not initialized. Cannot get pod events.")
            return []
    try:
        since_time = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        field_selector = f"involvedObject.kind=Pod,involvedObject.name={pod_name}"
        events = await k8s_core_v1.list_namespaced_event(namespace=namespace, field_selector=field_selector, limit=limit * 2, _request_timeout=20)

        recent_events = []
        if events and events.items:
                sorted_events = sorted(
                    events.items,
                    key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp or datetime(MINYEAR, 1, 1, tzinfo=timezone.utc),
                    reverse=True
                )
                for event in sorted_events:
                    event_time = event.last_timestamp or event.event_time or event.metadata.creation_timestamp
                    if not event_time: continue

                    if event_time.tzinfo is None:
                        event_time = event_time.replace(tzinfo=timezone.utc)

                    if event_time >= since_time:
                        recent_events.append({
                            "time": event_time.isoformat(), "type": event.type, "reason": event.reason,
                            "message": event.message, "count": event.count,
                            "source_component": event.source.component if event.source else 'N/A',
                            "reporting_controller": event.reporting_component or 'N/A',
                        })
                    if len(recent_events) >= limit:
                        break
        return recent_events
    except ApiException as e:
        if e.status != 403:
            logging.warning(f"[K8s Monitor] API error listing events for pod {namespace}/{pod_name}: {e.status} {e.reason}")
        return []
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error listing events for pod {namespace}/{pod_name}: {e}", exc_info=True)
        return []


async def get_controller_info(namespace, owner_references):
    """Lấy thông tin của controller sở hữu Pod asynchronously."""
    # ... (giữ nguyên code) ...
    if not k8s_client_initialized or not owner_references:
        return None

    controller_info = {}
    tasks = []

    async def fetch_single_controller(owner):
        kind = owner.get('kind')
        name = owner.get('name')
        if not kind or not name: return None, None

        controller_key = f"{kind}/{name}"
        controller_data = {"kind": kind, "name": name, "status": "N/A", "details": {}}
        details = {}

        try:
            if kind == 'ReplicaSet' and k8s_apps_v1:
                rs = await k8s_apps_v1.read_namespaced_replica_set(name=name, namespace=namespace, _request_timeout=10)
                details = {"replicas": rs.status.replicas or 0, "ready_replicas": rs.status.ready_replicas or 0, "available_replicas": rs.status.available_replicas or 0, "conditions": {c.type: c.status for c in rs.status.conditions} if rs.status.conditions else {}}
                if rs.metadata.owner_references:
                    for rs_owner in rs.metadata.owner_references:
                        if rs_owner.kind == 'Deployment': details['parent_deployment'] = rs_owner.name
            elif kind == 'Deployment' and k8s_apps_v1:
                dep = await k8s_apps_v1.read_namespaced_deployment(name=name, namespace=namespace, _request_timeout=10)
                details = {"replicas": dep.status.replicas or 0, "ready_replicas": dep.status.ready_replicas or 0, "available_replicas": dep.status.available_replicas or 0, "updated_replicas": dep.status.updated_replicas or 0, "unavailable_replicas": dep.status.unavailable_replicas or 0, "conditions": {c.type: c.status for c in dep.status.conditions} if dep.status.conditions else {}, "strategy": dep.spec.strategy.type if dep.spec.strategy else "N/A"}
            elif kind == 'StatefulSet' and k8s_apps_v1:
                sts = await k8s_apps_v1.read_namespaced_stateful_set(name=name, namespace=namespace, _request_timeout=10)
                details = {"replicas": sts.status.replicas or 0, "ready_replicas": sts.status.ready_replicas or 0, "current_replicas": sts.status.current_replicas or 0, "updated_replicas": sts.status.updated_replicas or 0, "conditions": {c.type: c.status for c in sts.status.conditions} if sts.status.conditions else {}}
            elif kind == 'DaemonSet' and k8s_apps_v1:
                 ds = await k8s_apps_v1.read_namespaced_daemon_set(name=name, namespace=namespace, _request_timeout=10)
                 details = {"desired_number_scheduled": ds.status.desired_number_scheduled or 0, "current_number_scheduled": ds.status.current_number_scheduled or 0, "number_ready": ds.status.number_ready or 0, "number_available": ds.status.number_available or 0, "updated_number_scheduled": ds.status.updated_number_scheduled or 0}
            elif kind == 'Job' and k8s_batch_v1:
                 job = await k8s_batch_v1.read_namespaced_job(name=name, namespace=namespace, _request_timeout=10)
                 details = {"succeeded": job.status.succeeded or 0, "failed": job.status.failed or 0, "active": job.status.active or 0, "conditions": {c.type: c.status for c in job.status.conditions} if job.status.conditions else {}}
                 if job.metadata.owner_references:
                     for job_owner in job.metadata.owner_references:
                         if job_owner.kind == 'CronJob': details['parent_cronjob'] = job_owner.name

            if details:
                 conditions = details.get("conditions", {})
                 if conditions.get("Available") == "False" or conditions.get("Progressing") == "False" or details.get("failed", 0) > 0: controller_data["status"] = "Problem"
                 elif conditions.get("Available") == "True" and conditions.get("Progressing") == "True": controller_data["status"] = "Healthy"
                 elif details.get("succeeded", 0) > 0 and details.get("active", 0) == 0 and details.get("failed", 0) == 0: controller_data["status"] = "Completed"
            controller_data["details"] = details
            return controller_key, controller_data

        except ApiException as e:
            if e.status != 404: logging.warning(f"[K8s Monitor] API error getting controller {kind}/{name}: {e.status} {e.reason}")
            controller_data["status"] = "ErrorFetching"; controller_data["error"] = f"{e.status} {e.reason}"
            return controller_key, controller_data
        except Exception as e:
            logging.error(f"[K8s Monitor] Unexpected error getting controller {kind}/{name}: {e}", exc_info=True)
            controller_data["status"] = "ErrorFetching"; controller_data["error"] = str(e)
            return controller_key, controller_data

    for owner in owner_references:
        tasks.append(fetch_single_controller(owner))

    results = await asyncio.gather(*tasks)
    for key, data in results:
        if key and data:
            controller_info[key] = data

    return controller_info if controller_info else None


async def get_namespace_resource_info(namespace):
    """Lấy thông tin ResourceQuota và LimitRange của namespace asynchronously."""
    # ... (giữ nguyên code) ...
    if not k8s_client_initialized or not k8s_core_v1:
        if not await initialize_k8s_client():
            logging.warning("[K8s Monitor] K8s client not initialized. Cannot get NS resource info.")
            return {}

    resource_info = {"quotas": [], "limits": []}
    quota_task = None
    limit_task = None

    try:
        quota_task = asyncio.create_task(k8s_core_v1.list_namespaced_resource_quota(namespace=namespace, _request_timeout=10))
    except Exception as e:
         logging.error(f"[K8s Monitor] Error creating task for ResourceQuotas in {namespace}: {e}", exc_info=True)

    try:
        limit_task = asyncio.create_task(k8s_core_v1.list_namespaced_limit_range(namespace=namespace, _request_timeout=10))
    except Exception as e:
         logging.error(f"[K8s Monitor] Error creating task for LimitRanges in {namespace}: {e}", exc_info=True)

    quota_results, limit_results = await asyncio.gather(quota_task, limit_task, return_exceptions=True)

    if isinstance(quota_results, ApiException):
         if quota_results.status != 403: logging.warning(f"[K8s Monitor] API error listing ResourceQuotas in {namespace}: {quota_results.status} {quota_results.reason}")
    elif isinstance(quota_results, Exception):
         logging.error(f"[K8s Monitor] Unexpected error listing ResourceQuotas in {namespace}: {quota_results}", exc_info=True)
    elif quota_results and quota_results.items:
         for quota in quota_results.items:
             resource_info["quotas"].append({"name": quota.metadata.name, "hard": quota.spec.hard if quota.spec and quota.spec.hard else {}, "used": quota.status.used if quota.status and quota.status.used else {}})

    if isinstance(limit_results, ApiException):
         if limit_results.status != 403: logging.warning(f"[K8s Monitor] API error listing LimitRanges in {namespace}: {limit_results.status} {limit_results.reason}")
    elif isinstance(limit_results, Exception):
         logging.error(f"[K8s Monitor] Unexpected error listing LimitRanges in {namespace}: {limit_results}", exc_info=True)
    elif limit_results and limit_results.items:
         for limit in limit_results.items:
             limit_details = []
             if limit.spec and limit.spec.limits:
                 for item in limit.spec.limits:
                     limit_details.append({"type": item.type, "max": item.max, "min": item.min, "default": item.default, "defaultRequest": item.default_request})
             resource_info["limits"].append({"name": limit.metadata.name, "limit_details": limit_details})

    return resource_info if resource_info["quotas"] or resource_info["limits"] else None


async def format_k8s_context(pod_info, node_info, pod_events):
    """Định dạng ngữ cảnh K8s chi tiết hơn asynchronously."""
    # ... (giữ nguyên code) ...
    context_lines = ["--- Kubernetes Context ---"]
    if not k8s_client_initialized:
        context_lines.append("K8s Client not initialized.")
        return "\n".join(context_lines)

    namespace = pod_info.get('namespace', 'N/A') if pod_info else 'N/A'

    if pod_info:
        # ... (phần thông tin pod giữ nguyên) ...
        context_lines.append(f"Pod: {namespace}/{pod_info.get('name', 'N/A')}")
        context_lines.append(f"  Status: {pod_info.get('status', 'N/A')} (Reason: {pod_info.get('reason', 'N/A')}, Message: {pod_info.get('message', 'N/A')})")
        context_lines.append(f"  Node: {pod_info.get('node_name', 'N/A')}")
        context_lines.append(f"  Restarts: {pod_info.get('restarts', 0)}")
        context_lines.append(f"  StartTime: {pod_info.get('start_time', 'N/A')}")

        if pod_info.get('container_statuses'):
                context_lines.append("  Container Statuses:")
                for name, status in pod_info['container_statuses'].items():
                    line = f"    - {name}: {status.get('state', 'N/A')} (Ready: {status.get('ready', 'N/A')}, Restarts: {status.get('restart_count', 0)})"
                    terminated_details = status.get('terminated_details')
                    if terminated_details and terminated_details.get('reason'):
                         line += f" [Terminated: {terminated_details['reason']} at {terminated_details.get('finished_at','N/A') or terminated_details.get('started_at','N/A')}]"
                    last_terminated_details = status.get('last_terminated_details')
                    if last_terminated_details and last_terminated_details.get('reason'):
                        line += f" [LastTerminated: {last_terminated_details['reason']} at {last_terminated_details.get('finished_at','N/A') or last_terminated_details.get('started_at','N/A')}]"
                    context_lines.append(line)

        if pod_info.get('conditions'):
                all_conditions = [f"{ctype}({cinfo.get('status','N/A')}-{cinfo.get('reason','N/A')})" for ctype, cinfo in pod_info['conditions'].items()]
                context_lines.append(f"  Pod Conditions: {', '.join(all_conditions)}")

        controller_info = await get_controller_info(namespace, pod_info.get('owner_references', []))
        if controller_info:
             context_lines.append("  Owner Controllers:")
             for c_key, c_data in controller_info.items():
                  details_str = json.dumps(c_data.get('details',{}), separators=(',', ':'))
                  context_lines.append(f"    - {c_key}: Status={c_data.get('status','N/A')}, Details={details_str}")

    else:
        context_lines.append("Pod Info: Not Available")

    if namespace != 'N/A':
        ns_resource_info = await get_namespace_resource_info(namespace)
        if ns_resource_info:
            context_lines.append(f"  Namespace '{namespace}' Resources:")
            if ns_resource_info.get("quotas"):
                 context_lines.append("    Quotas:")
                 for q in ns_resource_info["quotas"]:
                     hard = json.dumps(q.get('hard',{}), separators=(',', ':'))
                     used = json.dumps(q.get('used',{}), separators=(',', ':'))
                     context_lines.append(f"      - {q.get('name')}: Hard={hard}, Used={used}")
            if ns_resource_info.get("limits"):
                 context_lines.append("    Limits:")
                 for l in ns_resource_info["limits"]:
                     details = json.dumps(l.get('limit_details',[]), separators=(',', ':'))
                     context_lines.append(f"      - {l.get('name')}: {details}")

    if node_info:
        # ... (phần thông tin node giữ nguyên) ...
        context_lines.append(f"Node Info ({node_info.get('name', 'N/A')}):")
        context_lines.append(f"  Kubelet: {node_info.get('kubelet_version', 'N/A')}")
        context_lines.append(f"  OS: {node_info.get('os_image', 'N/A')}")
        context_lines.append(f"  Kernel: {node_info.get('kernel_version', 'N/A')}")
        context_lines.append(f"  Allocatable: CPU={node_info.get('allocatable_cpu', 'N/A')}, Mem={node_info.get('allocatable_memory', 'N/A')}")
        if node_info.get('conditions'):
                all_node_conditions = [f"{ctype}({cinfo.get('status','N/A')}-{cinfo.get('reason','N/A')})" for ctype, cinfo in node_info['conditions'].items()]
                context_lines.append(f"  Node Conditions: {', '.join(all_node_conditions)}")


    if pod_events:
        # ... (phần thông tin event giữ nguyên) ...
        context_lines.append(f"Recent Pod Events (max {len(pod_events)}):")
        for event in pod_events:
            message_preview = event.get('message', '')
            if len(message_preview) > 200:
                 message_preview = message_preview[:197] + '...'
            context_lines.append(f"  - [{event.get('time', 'N/A')}] {event.get('type', 'N/A')} Reason={event.get('reason', 'N/A')} Count={event.get('count',1)} From={event.get('source_component', 'N/A')}/{event.get('reporting_controller','N/A')}: {message_preview}")


    context_lines.append("--- End Context ---")
    return "\n".join(context_lines)


async def scan_kubernetes_for_issues(namespaces_to_scan, restart_threshold, loki_detail_log_range_minutes=30):
    """Scans Kubernetes namespaces for problematic pods asynchronously."""
    # ... (giữ nguyên code) ...
    if not k8s_client_initialized or not k8s_core_v1:
        if not await initialize_k8s_client():
            logging.warning("[K8s Monitor] K8s client not initialized. Skipping K8s scan.")
            return {}

    problematic_pods = {}
    logging.info(f"[K8s Monitor] Scanning {len(namespaces_to_scan)} Kubernetes namespaces for problematic pods (async)...")
    recent_termination_threshold = datetime.now(timezone.utc) - timedelta(minutes=loki_detail_log_range_minutes)

    tasks = []
    for ns in namespaces_to_scan:
        tasks.append(asyncio.create_task(scan_single_namespace(ns, restart_threshold, recent_termination_threshold)))

    results = await asyncio.gather(*tasks)

    for ns_result in results:
        problematic_pods.update(ns_result)

    logging.info(f"[K8s Monitor] Finished K8s scan (async). Found {len(problematic_pods)} potentially problematic pods.")
    return problematic_pods


async def scan_single_namespace(ns, restart_threshold, recent_termination_threshold):
    """Scans a single namespace for problematic pods asynchronously."""
    # ... (giữ nguyên code) ...
    ns_problems = {}
    try:
        pods = await k8s_core_v1.list_namespaced_pod(namespace=ns, watch=False, _request_timeout=60)
        for pod in pods.items:
            pod_key = f"{ns}/{pod.metadata.name}"
            issue_found = False
            reason = ""

            if pod.status.phase in ["Failed", "Unknown"]:
                issue_found = True; reason = f"Pod phase is {pod.status.phase}"
            elif pod.status.phase == "Pending" and pod.status.conditions:
                scheduled_condition = next((c for c in pod.status.conditions if c.type == "PodScheduled"), None)
                if scheduled_condition and scheduled_condition.status == "False" and scheduled_condition.reason == "Unschedulable":
                    issue_found = True; reason = f"Pod is Unschedulable ({scheduled_condition.message or 'No details'})"

            if not issue_found and pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    if cs.restart_count >= restart_threshold:
                        issue_found = True; reason = f"Container '{cs.name}' restarted {cs.restart_count} times (>= threshold {restart_threshold})"; break
                    if cs.state and cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CreateContainerError", "InvalidImageName"]:
                        issue_found = True; reason = f"Container '{cs.name}' in Waiting state ({cs.state.waiting.reason})"; break
                    if cs.state and cs.state.terminated and cs.state.terminated.reason in ["OOMKilled", "Error", "ContainerCannotRun", "DeadlineExceeded"]:
                        is_recent = False
                        finished_at = cs.state.terminated.finished_at
                        if finished_at and finished_at.tzinfo is None: finished_at = finished_at.replace(tzinfo=timezone.utc)
                        if finished_at and finished_at >= recent_termination_threshold: is_recent = True
                        if is_recent or pod.spec.restart_policy != "Always":
                            issue_found = True; reason = f"Container '{cs.name}' Terminated (Reason: {cs.state.terminated.reason}, ExitCode: {cs.state.terminated.exit_code}, Recent: {is_recent})"; break

            if issue_found:
                logging.warning(f"[K8s Monitor] Found potentially problematic pod: {pod_key}. Reason: {reason}")
                if pod_key not in ns_problems:
                    ns_problems[pod_key] = {"namespace": ns, "pod_name": pod.metadata.name, "reason": f"K8s: {reason}"}
                elif "Terminated" in reason or "CrashLoopBackOff" in reason:
                     ns_problems[pod_key]["reason"] = f"K8s: {reason}"
    except ApiException as e:
        logging.error(f"[K8s Monitor] API Error scanning namespace {ns} (async): {e.status} {e.reason}")
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error scanning namespace {ns} (async): {e}", exc_info=True)
    return ns_problems


async def get_active_namespaces(excluded_namespaces):
    """Lấy danh sách namespace đang hoạt động asynchronously."""
    # ... (giữ nguyên code) ...
    if not k8s_client_initialized or not k8s_core_v1:
        if not await initialize_k8s_client():
            logging.warning("[K8s Monitor] K8s client not initialized. Cannot get active namespaces.")
            return []
    active_namespaces = []
    try:
        all_namespaces = await k8s_core_v1.list_namespace(watch=False, _request_timeout=60)
        for ns in all_namespaces.items:
            if ns.status.phase == "Active" and ns.metadata.name not in excluded_namespaces:
                active_namespaces.append(ns.metadata.name)
        logging.info(f"[K8s Monitor] Found {len(active_namespaces)} active and non-excluded namespaces in cluster (async).")
    except ApiException as e:
        logging.error(f"[K8s Monitor] API Error listing namespaces (async): {e.status} {e.reason}. Check RBAC permissions.")
    except Exception as e:
        logging.error(f"[K8s Monitor] Unexpected error listing namespaces (async): {e}", exc_info=True)
    return active_namespaces


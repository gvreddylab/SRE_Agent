"""
Streamlit UI for the RCA Agent.

Run with:
    streamlit run app/ui/streamlit_app.py

Pages:
  - Chat / Analysis: trigger RCA with live step streaming.
  - Cluster Dashboard: health overview.
  - Incident History: browse past RCA incidents.
  - RCA Reports: view and download generated MD/PDF reports.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ── Bootstrap path so `app.*` imports work when running from repo root ──
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings
from app.llm.ollama_client import is_ollama_reachable, list_available_models

# ──────────────────────────────────────────────────────────────
# Page config (must be first Streamlit call)
# ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="gvreddy's SRE Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────────
# Custom CSS
# ──────────────────────────────────────────────────────────────

st.markdown("""
<style>
    /* Sidebar */
    [data-testid="stSidebar"] { background: #0f172a; }
    [data-testid="stSidebar"] * { color: #e2e8f0 !important; }

    /* Cards */
    .metric-card {
        background: #1e293b;
        border-radius: 12px;
        padding: 1.2rem;
        border-left: 4px solid #3b82f6;
        margin-bottom: 0.8rem;
    }

    /* Step badges */
    .step-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.78rem;
        font-weight: 600;
        margin: 2px 0;
    }
    .step-done  { background: #166534; color: #bbf7d0; }
    .step-run   { background: #1e40af; color: #bfdbfe; }
    .step-error { background: #991b1b; color: #fecaca; }

    /* RCA sections */
    .rca-section {
        background: #1e293b;
        border-radius: 8px;
        padding: 1rem 1.2rem;
        margin: 0.5rem 0;
        border-left: 3px solid #6366f1;
    }
    .rca-section h4 { color: #a5b4fc; margin: 0 0 0.5rem 0; }

    /* Severity badges */
    .sev-low      { color: #4ade80; font-weight: 700; }
    .sev-medium   { color: #facc15; font-weight: 700; }
    .sev-high     { color: #fb923c; font-weight: 700; }
    .sev-critical { color: #f87171; font-weight: 700; }

    /* Chat bubbles */
    .user-msg {
        background: #1e3a5f;
        border-radius: 12px 12px 2px 12px;
        padding: 0.8rem 1rem;
        margin: 0.5rem 0 0.5rem 3rem;
        color: #e2e8f0;
    }
    .agent-msg {
        background: #1e293b;
        border-radius: 12px 12px 12px 2px;
        padding: 0.8rem 1rem;
        margin: 0.5rem 3rem 0.5rem 0;
        color: #e2e8f0;
        border-left: 3px solid #6366f1;
    }

    /* Login / Signup card */
    .auth-card {
        background: #1e293b;
        border-radius: 16px;
        padding: 2.5rem 2rem;
        border: 1px solid #334155;
        box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        max-width: 420px;
        margin: 2rem auto;
    }
    .auth-logo {
        text-align: center;
        font-size: 2.5rem;
        margin-bottom: 0.2rem;
    }
    .auth-title {
        text-align: center;
        color: #e2e8f0;
        font-size: 1.5rem;
        font-weight: 700;
        margin-bottom: 0.3rem;
    }
    .auth-sub {
        text-align: center;
        color: #94a3b8;
        font-size: 0.88rem;
        margin-bottom: 1.5rem;
    }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# Session state initialisation
# ──────────────────────────────────────────────────────────────

def _init_session() -> None:
    defaults = {
        "chat_messages": [],
        "last_rca": None,
        "analysis_running": False,
        "selected_model": settings.ollama.default_model,
        "available_models": [],
        "available_namespaces": [],
        "pending_action": None,
        # auth
        "authenticated": False,
        "current_user": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_session()


# ──────────────────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────────────────

def render_sidebar() -> tuple[str, str]:
    with st.sidebar:
        st.markdown("## gvreddy's SRE Agent")
        _team_img = Path(__file__).parent.parent.parent / "data" / "assets" / "team.png"
        if _team_img.exists():
            st.image(str(_team_img), use_container_width=True)
        st.divider()

        # Logged-in user info + sign-out
        user = st.session_state.get("current_user")
        if user:
            st.markdown(
                f"👤 **{user.full_name}**  \n"
                f"<span style='color:#94a3b8;font-size:0.8rem'>{user.role} · {user.username}</span>",
                unsafe_allow_html=True,
            )
            if st.button("🚪 Sign Out", use_container_width=True):
                st.session_state.authenticated = False
                st.session_state.current_user  = None
                st.rerun()
            st.divider()

        # Model kept in session state — not exposed in UI
        if not st.session_state.available_models:
            st.session_state.available_models = list_available_models()
        if "model_selector" not in st.session_state:
            default = st.session_state.selected_model
            if default in st.session_state.available_models:
                st.session_state["model_selector"] = default
        selected_model = st.session_state.get("model_selector", settings.ollama.default_model)
        st.session_state.selected_model = selected_model

    # Always scan all namespaces
    namespace = "all"
    return namespace, selected_model


# ──────────────────────────────────────────────────────────────
# Page: Chat / RCA Analysis
# ──────────────────────────────────────────────────────────────

def page_analysis(namespace: str, model: str) -> None:
    st.title("🔍 RCA Agent")

    col_run, col_spacer = st.columns([3, 7])
    with col_run:
        trigger = st.button(
            "🚀 Run RCA Analysis",
            disabled=st.session_state.analysis_running,
            use_container_width=True,
            type="primary",
        )

    if trigger and not st.session_state.analysis_running:
        _run_analysis(namespace=namespace, resource=None, model=model)

    # Display chat history
    _render_chat()

    # ── Allow / Deny action buttons ───────────────────────────
    if st.session_state.get("pending_action"):
        st.markdown("")
        btn_col1, btn_col2, btn_spacer = st.columns([1, 1, 5])
        with btn_col1:
            if st.button("✅ Allow", type="primary", use_container_width=True, key="allow_btn"):
                result = _execute_action(st.session_state.pending_action)
                st.session_state.pending_action = None
                st.session_state.chat_messages.append({"role": "assistant", "content": result})
                st.rerun()
        with btn_col2:
            if st.button("❌ Deny", type="secondary", use_container_width=True, key="deny_btn"):
                st.session_state.pending_action = None
                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": "❌ Action cancelled — no changes were made.",
                })
                st.rerun()

    # Display last RCA result
    if st.session_state.last_rca:
        _render_rca_result(st.session_state.last_rca)

    # ── Natural-language query box ──────────────────────────────
    st.divider()
    st.markdown("#### Ask about your Infrastructure and Applications")
    st.caption(
        "Ask questions or take actions — e.g. **restart argocd repo pod**, "
        "**scale argocd-server to 2**, **get logs argocd-server**, **delete pod X**. "
        "The agent will ask for **allow / deny** before executing."
    )
    with st.form(key="query_form", clear_on_submit=True):
        user_query = st.text_area(
            "Query",
            placeholder=(
                "What is the cluster health?\n"
                "Can you restart the argocd repo pod?\n"
                "Scale argocd-server to 2 replicas\n"
                "Get logs for argocd-server\n"
                "Rollout restart argocd-repo-server deployment"
            ),
            height=110,
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("Send", use_container_width=True, type="primary")

    if submitted and user_query.strip():
        _handle_cluster_query(user_query.strip(), namespace, model)
        st.rerun()


def _fuzzy_match_resource(words: list[str], resource_map: dict[str, str]) -> tuple[str, str] | None:
    """Return (name, namespace) for the resource whose name best matches the query words."""
    import re as _re
    # Normalise: split resource names on - and match against query tokens
    query_tokens = {w.lower() for w in words if len(w) > 2}
    best_name, best_ns, best_score = None, None, 0
    for name, ns in resource_map.items():
        name_tokens = set(_re.split(r"[-.]", name.lower()))
        score = len(query_tokens & name_tokens)
        if score > best_score:
            best_score, best_name, best_ns = score, name, ns
    return (best_name, best_ns) if best_score > 0 else None


def _handle_cluster_query(query: str, namespace: str, model: str) -> None:
    """Fetch relevant cluster data or execute a confirmed action."""
    st.session_state.chat_messages.append({"role": "user", "content": query})
    q = query.lower().strip()

    # ── Allow / Deny handling for pending actions ─────────────
    pending = st.session_state.get("pending_action")
    _allow_words = {"allow", "yes", "confirm", "ok", "proceed", "do it", "sure", "go", "approve"}
    _deny_words  = {"deny", "no", "cancel", "stop", "abort", "skip", "reject", "nope"}

    if pending:
        if any(w in q for w in _allow_words):
            result = _execute_action(pending)
            st.session_state.pending_action = None
            st.session_state.chat_messages.append({"role": "assistant", "content": result})
            return
        if any(w in q for w in _deny_words):
            st.session_state.pending_action = None
            st.session_state.chat_messages.append({
                "role": "assistant",
                "content": "❌ Action cancelled — no changes were made.",
            })
            return
        # User typed something else — clear pending and handle normally
        st.session_state.pending_action = None

    q = query.lower()
    lines: list[str] = []

    try:
        from app.tools.kubernetes_tools import (
            list_unhealthy_pods, get_node_status, get_namespace_events,
            describe_pod, get_deployment_status, get_all_namespaces,
            cluster_health_summary, get_core_api, get_apps_api,
        )
        import re as _re

        core = get_core_api()

        # ── Cluster health / status / overview ────────────────
        if any(w in q for w in ["health", "cluster", "overview", "status", "summary", "how is"]):
            h = cluster_health_summary()
            overall = "✅ Healthy" if h["unhealthy_pods"] == 0 else f"⚠️ Degraded ({h['unhealthy_pods']} issue(s))"
            lines.append(f"**Cluster Status:** {overall}")
            lines.append(f"- Nodes: {h['ready_nodes']}/{h['total_nodes']} ready")
            lines.append(f"- Unhealthy pods: {h['unhealthy_pods']}")
            if h["issue_breakdown"]:
                lines.append("- Issue breakdown:")
                for issue, count in h["issue_breakdown"].items():
                    issue_str = issue.value if hasattr(issue, "value") else str(issue)
                    lines.append(f"  - {issue_str}: {count}")

        # ── Unhealthy / failing pods ──────────────────────────
        if any(kw in q for kw in [
            "fail", "crash", "error", "unhealthy", "not running", "not ready",
            "pending", "oomkill", "imagepull", "evict", "broken", "issue", "problem",
        ]):
            bad_pods = list_unhealthy_pods(namespace)
            if bad_pods:
                lines.append(f"**Unhealthy pods in `{namespace}`:** ({len(bad_pods)} found)")
                for p in bad_pods:
                    issue = p.issue_type.value if hasattr(p.issue_type, "value") else str(p.issue_type)
                    lines.append(
                        f"- `{p.namespace}/{p.name}` | Phase: `{p.phase}` "
                        f"| Issue: `{issue}` | Restarts: {p.restart_count}"
                    )
                lines.append("\n> To investigate further, select the namespace above and click **🚀 Run RCA Analysis**.")
            else:
                lines.append(f"✅ **No unhealthy pods** found in `{namespace}` — all pods are running normally.")

        # ── List all pods ──────────────────────────────────────
        if any(kw in q for kw in ["list pod", "show pod", "get pod", "all pod", "list all", "show all"]):
            all_pods = (
                core.list_pod_for_all_namespaces()
                if namespace == "all"
                else core.list_namespaced_pod(namespace=namespace)
            )
            lines.append(f"**Pods in `{namespace}`:** ({len(all_pods.items)} total)")
            for p in all_pods.items[:40]:
                phase = (p.status.phase or "Unknown") if p.status else "Unknown"
                icon = "✅" if phase in ("Running", "Succeeded") else "⚠️"
                lines.append(f"- {icon} `{p.metadata.namespace}/{p.metadata.name}` | {phase}")
            if len(all_pods.items) > 40:
                lines.append(f"  _(showing first 40 of {len(all_pods.items)})_")

        # ── Describe a specific pod by name ───────────────────
        if any(w in q for w in ["describe", "detail", "info"]) and "pod" in q:
            words = _re.findall(r"[\w][\w\-\.]+", query)
            try:
                all_pods = (
                    core.list_pod_for_all_namespaces()
                    if namespace == "all"
                    else core.list_namespaced_pod(namespace=namespace)
                )
                pod_map = {p.metadata.name: p.metadata.namespace for p in all_pods.items}
            except Exception:
                pod_map = {}
            for word in words:
                if word in pod_map:
                    pod_ns = pod_map[word]
                    info = describe_pod(word, pod_ns)
                    lines.append(f"**Pod: `{pod_ns}/{word}`**")
                    lines.append(f"- Phase: `{info.get('phase')}`")
                    lines.append(f"- Node: `{info.get('node')}`")
                    lines.append(f"- IP: `{info.get('ip')}`")
                    for cs in info.get("container_statuses", []):
                        lines.append(
                            f"- Container `{cs['name']}`: ready={cs['ready']}, "
                            f"restarts={cs['restart_count']}, state={cs.get('state')}"
                        )
                    break

        # ── Deployments ───────────────────────────────────────
        if any(w in q for w in ["deployment", "deploy", "replica", "rollout"]):
            target_ns = namespace if namespace != "all" else "default"
            deploys = get_deployment_status(namespace=target_ns)
            if deploys:
                lines.append(f"**Deployments in `{target_ns}`:**")
                for d in deploys:
                    icon = "✅" if d.ready == d.desired else "⚠️"
                    lines.append(f"- {icon} `{d.name}` | desired={d.desired} ready={d.ready} available={d.available}")
            else:
                lines.append(f"No deployments found in `{target_ns}`.")

        # ── Services ──────────────────────────────────────────
        if any(w in q for w in ["service", "svc"]):
            target_ns = namespace if namespace != "all" else "default"
            svcs = core.list_namespaced_service(namespace=target_ns)
            lines.append(f"**Services in `{target_ns}`:** ({len(svcs.items)} found)")
            for s in svcs.items:
                stype = s.spec.type if s.spec else "—"
                ports = ", ".join(f"{p.port}/{p.protocol}" for p in (s.spec.ports or [])) if s.spec else "—"
                lines.append(f"- `{s.metadata.name}` | {stype} | ports: {ports}")

        # ── Nodes ─────────────────────────────────────────────
        if any(w in q for w in ["node", "nodes", "worker", "master", "control plane"]):
            nodes = get_node_status()
            lines.append(f"**Nodes:** ({len(nodes)} total)")
            for n in nodes:
                icon = "✅" if n.ready else "❌"
                lines.append(f"- {icon} `{n.name}` | roles={n.roles} | version={n.version}")

        # ── Events / warnings ─────────────────────────────────
        if any(w in q for w in ["event", "warning", "alert"]):
            target_ns = namespace if namespace != "all" else "default"
            events = get_namespace_events(namespace=target_ns, warning_only=True, limit=10)
            if events:
                lines.append(f"**Recent warning events in `{target_ns}`:**")
                for e in events:
                    lines.append(f"- `{e.reason}` on `{e.regarding_kind}/{e.regarding_name}`: {e.message[:120]}")
            else:
                lines.append(f"No warning events in `{target_ns}`.")

        # ── Namespaces ────────────────────────────────────────
        if any(w in q for w in ["namespace", "namespaces", " ns "]):
            nss = get_all_namespaces()
            lines.append(f"**Namespaces ({len(nss)}):** {', '.join(f'`{n}`' for n in nss)}")

        # ── Actions ───────────────────────────────────────────
        _action_verbs = [
            "rollout restart", "rollout",
            "restart", "reboot", "bounce",
            "delete", "remove", "kill",
            "scale", "resize",
            "get log", "fetch log", "show log", "tail log", "log",
        ]
        detected_action = next((v for v in _action_verbs if v in q), None)

        if detected_action:
            # Build live resource maps
            pod_map: dict[str, str] = {}
            dep_map: dict[str, str] = {}
            try:
                all_p = core.list_pod_for_all_namespaces()
                pod_map = {p.metadata.name: p.metadata.namespace for p in all_p.items}
                apps = get_apps_api()
                all_d = apps.list_deployment_for_all_namespaces()
                dep_map = {d.metadata.name: d.metadata.namespace for d in all_d.items}
            except Exception:
                pass

            words = _re.findall(r"[\w][\w\-\.]+", query)

            # Fuzzy-match resource from query words
            pod_match  = _fuzzy_match_resource(words, pod_map)
            dep_match  = _fuzzy_match_resource(words, dep_map)

            def _ask(action_type: str, resource: str, ns: str,
                     description: str, extra: dict | None = None) -> None:
                st.session_state.pending_action = {
                    "type": action_type, "resource": resource,
                    "namespace": ns, "resource_type": "pod" if "pod" in action_type else "deployment",
                    "extra": extra or {},
                }
                lines.append(
                    f"I found **`{ns}/{resource}`**.\n\n"
                    f"⚠️ Action: **{description}**\n\n"
                    "Use the **Allow** or **Deny** buttons below to confirm."
                )

            # ── Restart / reboot ─────────────────────────────
            if detected_action in ("restart", "reboot", "bounce"):
                if "deployment" in q and dep_match:
                    _ask("rollout_restart", dep_match[0], dep_match[1],
                         f"Rollout restart deployment `{dep_match[1]}/{dep_match[0]}`")
                elif pod_match:
                    _ask("restart", pod_match[0], pod_match[1],
                         f"Delete pod `{pod_match[1]}/{pod_match[0]}` (K8s will recreate it automatically)")
                elif dep_match:
                    _ask("rollout_restart", dep_match[0], dep_match[1],
                         f"Rollout restart deployment `{dep_match[1]}/{dep_match[0]}`")
                else:
                    lines.append("⚠️ I couldn't find a matching pod or deployment. Try being more specific.")

            # ── Rollout restart ──────────────────────────────
            elif "rollout" in detected_action:
                if dep_match:
                    _ask("rollout_restart", dep_match[0], dep_match[1],
                         f"Rollout restart deployment `{dep_match[1]}/{dep_match[0]}`")
                elif pod_match:
                    # Restart the owning deployment if we can find it
                    _ask("restart", pod_match[0], pod_match[1],
                         f"Restart pod `{pod_match[1]}/{pod_match[0]}`")
                else:
                    lines.append("⚠️ I couldn't find a matching deployment.")

            # ── Scale ────────────────────────────────────────
            elif detected_action in ("scale", "resize"):
                replica_match = _re.search(r"\b(\d+)\b", query)
                replicas = int(replica_match.group(1)) if replica_match else None
                if dep_match and replicas is not None:
                    _ask("scale", dep_match[0], dep_match[1],
                         f"Scale deployment `{dep_match[1]}/{dep_match[0]}` to **{replicas}** replica(s)",
                         extra={"replicas": replicas})
                elif not dep_match:
                    lines.append("⚠️ I couldn't find a matching deployment to scale.")
                else:
                    lines.append("⚠️ Please specify a replica count — e.g. `scale argocd-server to 2`.")

            # ── Delete ───────────────────────────────────────
            elif detected_action in ("delete", "remove", "kill"):
                if pod_match:
                    _ask("delete", pod_match[0], pod_match[1],
                         f"**Permanently delete** pod `{pod_match[1]}/{pod_match[0]}`")
                else:
                    lines.append("⚠️ I couldn't find a matching pod to delete.")

            # ── Logs ─────────────────────────────────────────
            elif "log" in detected_action:
                target = pod_match or dep_match
                if target:
                    name, ns_r = target
                    # If deployment matched, find one of its pods
                    if target == dep_match and name in dep_map:
                        try:
                            pods_in_dep = [
                                p for p in core.list_namespaced_pod(namespace=ns_r).items
                                if any(name in (p.metadata.labels or {}).get("app", "") or
                                       name.split("-")[0] in p.metadata.name
                                       for _ in [1])
                            ]
                            if pods_in_dep:
                                name = pods_in_dep[0].metadata.name
                        except Exception:
                            pass
                    try:
                        log_text = core.read_namespaced_pod_log(
                            name=name, namespace=ns_r, tail_lines=60,
                        )
                        lines.append(f"**Logs — `{ns_r}/{name}` (last 60 lines):**")
                        lines.append(f"```\n{log_text.strip() or '(no output)'}\n```")
                    except Exception as exc:
                        # Try previous container logs on crash
                        try:
                            log_text = core.read_namespaced_pod_log(
                                name=name, namespace=ns_r, tail_lines=60, previous=True,
                            )
                            lines.append(f"**Previous container logs — `{ns_r}/{name}`:**")
                            lines.append(f"```\n{log_text.strip() or '(no output)'}\n```")
                        except Exception:
                            lines.append(f"⚠️ Could not fetch logs: `{exc}`")
                else:
                    lines.append("⚠️ I couldn't find a matching pod. Try mentioning part of the pod or deployment name.")

    except Exception as exc:
        lines.append(f"⚠️ Error gathering cluster data: `{exc}`")

    if not lines:
        lines.append(
            "Try asking about: **cluster health**, **pods**, **deployments**, "
            "**services**, **nodes**, **events**, or **namespaces**.\n\n"
            "**Actions:** `restart pod <name>`, `scale deployment <name> to <n> replicas`, "
            "`rollout restart deployment <name>`, `delete pod <name>`, `get logs <pod-name>`\n\n"
            "To run a full RCA analysis, click **🚀 Run RCA Analysis** above."
        )

    st.session_state.chat_messages.append({"role": "assistant", "content": "\n".join(lines)})


def _execute_action(action: dict) -> str:
    """Execute a confirmed K8s action and return a result message."""
    from app.tools.kubernetes_tools import get_core_api, get_apps_api
    from datetime import datetime, timezone

    act_type = action["type"]
    resource = action["resource"]
    ns = action["namespace"]
    extra = action.get("extra", {})

    try:
        core = get_core_api()
        apps = get_apps_api()

        if act_type == "restart":
            # Delete pod — controller recreates it
            core.delete_namespaced_pod(name=resource, namespace=ns)
            return (
                f"✅ **Pod restarted** — `{ns}/{resource}` has been deleted.\n\n"
                "Kubernetes will recreate it automatically. Run **🚀 Run RCA Analysis** "
                "in a few seconds to confirm recovery."
            )

        elif act_type == "rollout_restart":
            # Patch deployment with restartedAt annotation to trigger rollout
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": now
                            }
                        }
                    }
                }
            }
            apps.patch_namespaced_deployment(name=resource, namespace=ns, body=patch)
            return (
                f"✅ **Rollout restart triggered** — deployment `{ns}/{resource}` is rolling out.\n\n"
                "New pods will be created and old ones terminated. Check status with "
                "`Show deployments in {ns}`."
            )

        elif act_type == "scale":
            replicas = extra.get("replicas", 1)
            patch = {"spec": {"replicas": replicas}}
            apps.patch_namespaced_deployment(name=resource, namespace=ns, body=patch)
            return (
                f"✅ **Deployment scaled** — `{ns}/{resource}` set to **{replicas}** replica(s).\n\n"
                "Kubernetes is reconciling the desired state."
            )

        elif act_type == "delete":
            core.delete_namespaced_pod(name=resource, namespace=ns)
            return f"✅ **Pod deleted** — `{ns}/{resource}` has been removed."

        else:
            return f"⚠️ Unknown action type: `{act_type}`"

    except Exception as exc:
        return f"❌ **Action failed:** `{exc}`"


def _render_pod_preview(namespace: str) -> None:
    """Show unhealthy pods for the selected namespace (triggered on demand)."""
    try:
        from app.tools.kubernetes_tools import list_unhealthy_pods
        with st.spinner(f"Scanning namespace '{namespace}' for unhealthy pods..."):
            pods = list_unhealthy_pods(namespace=namespace)
        if pods:
            st.success(f"Found **{len(pods)} unhealthy pod(s)** — these will be the focus of the analysis.")
            st.dataframe(
                [
                    {
                        "Pod": p.name,
                        "Namespace": p.namespace,
                        "Phase": p.phase,
                        "Issue": p.issue_type,
                        "Restarts": p.restart_count,
                        "Node": p.node_name or "—",
                    }
                    for p in pods
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(
                f"No unhealthy pods in **{namespace}**. "
                "The agent will still analyse events, nodes, and metrics. "
                "Try selecting **all** in the namespace dropdown to scan the whole cluster."
            )
    except Exception as exc:
        st.error(f"Could not scan pods: {exc}")


def _run_analysis(namespace: str, resource: str | None, model: str) -> None:
    st.session_state.analysis_running = True
    st.session_state.chat_messages.append({
        "role": "user",
        "content": f"Run RCA analysis on namespace `{namespace}`"
                   + (f", resource `{resource}`" if resource else ""),
    })

    st.markdown("### ⚙️ Agent Steps")
    step_container = st.container()
    progress_bar = st.progress(0)

    try:
        from app.agents.rca_agent import RCAAgent
        agent = RCAAgent()

        steps = [
            "gather_pods", "gather_logs", "gather_events", "gather_metrics",
            "gather_nodes", "query_memory", "build_context",
            "call_llm", "persist_results", "generate_reports",
        ]
        total_steps = len(steps)
        completed_steps: list[str] = []
        # Accumulate full state across all streaming updates — each partial
        # only contains the fields that changed in that node, so we merge
        # them all to get the final complete state (including rca_output from
        # call_llm, incident_id from persist_results, paths from generate_reports).
        accumulated_state: dict = {}

        for partial in agent.stream_analysis(
            namespace=namespace,
            resource_name=resource,
            model_name=model,
        ):
            node = partial.get("_node", "")
            messages = partial.get("messages", [])
            errors = partial.get("errors", [])

            accumulated_state.update(partial)

            if node:
                completed_steps.append(node)
                progress = len(completed_steps) / total_steps
                progress_bar.progress(min(progress, 1.0))

            with step_container:
                for msg in messages:
                    st.markdown(
                        f'<span class="step-badge step-done">{node}</span> {msg}',
                        unsafe_allow_html=True,
                    )
                for err in errors:
                    st.markdown(
                        f'<span class="step-badge step-error">ERROR</span> {err}',
                        unsafe_allow_html=True,
                    )

        progress_bar.progress(1.0)

        if accumulated_state:
            unhealthy = accumulated_state.get("unhealthy_pods", [])
            rca = accumulated_state.get("rca_output")

            if not unhealthy:
                # Cluster was healthy — no RCA needed
                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": (
                        f"✅ **Cluster is healthy** — no unhealthy pods found in `{namespace}`. "
                        "No RCA report was generated."
                    ),
                })
            elif rca:
                st.session_state.last_rca = accumulated_state
                sev_icon = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(
                    rca.severity, "⚪"
                )
                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": (
                        f"**RCA Complete** — `{rca.issue_type}` detected in `{namespace}`.\n\n"
                        f"{sev_icon} **Severity**: {rca.severity.upper()} | "
                        f"**Confidence**: {rca.confidence_score:.0%}\n\n"
                        f"{rca.executive_summary}\n\n"
                        f"Full report available in **📊 RCA Reports**."
                    ),
                })

    except Exception as exc:
        st.error(f"Analysis failed: {exc}")
        st.session_state.chat_messages.append({
            "role": "assistant",
            "content": f"❌ Analysis failed: {exc}",
        })
    finally:
        st.session_state.analysis_running = False


def _render_chat() -> None:
    if not st.session_state.chat_messages:
        st.markdown(
            '<div class="agent-msg">👋 I\'m your AI RCA Agent. Click <b>Run RCA Analysis</b> '
            "to scan your environment, identify issues, determine the root cause, and recommend fixes.</div>",
            unsafe_allow_html=True,
        )
        return

    for msg in st.session_state.chat_messages[-20:]:  # Show last 20 messages
        if msg["role"] == "user":
            st.markdown(
                f'<div class="user-msg">👤 {msg["content"]}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="agent-msg">🤖 {msg["content"]}</div>',
                unsafe_allow_html=True,
            )


def _render_rca_result(state: dict) -> None:
    rca = state.get("rca_output")
    if not rca:
        return

    st.divider()
    st.markdown("## 📊 Root Cause Analysis Report")

    sev_class = f"sev-{rca.severity}"
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Issue Type", rca.issue_type)
    col2.metric("Severity", rca.severity.upper())
    col3.metric("Confidence", f"{rca.confidence_score:.0%}")
    col4.metric("Incident ID", state.get("incident_id", "N/A")[:8] or "N/A")

    tabs = st.tabs([
        "📋 Summary", "🔎 Root Cause", "🛠️ Fix",
        "✅ Validation", "🛡️ Prevention", "📁 Download"
    ])

    with tabs[0]:
        st.markdown('<div class="rca-section">', unsafe_allow_html=True)
        st.markdown("#### Executive Summary")
        st.write(rca.executive_summary)
        st.markdown("#### Impact Assessment")
        st.write(rca.impact_assessment or "Not assessed.")
        st.markdown("#### Contributing Factors")
        st.write(rca.contributing_factors or "None identified.")
        st.markdown("</div>", unsafe_allow_html=True)

        # Similar incidents
        similars = state.get("similar_incidents", [])
        if similars:
            st.markdown("#### 🧠 Similar Past Incidents")
            for s in similars[:3]:
                with st.expander(f"{s.similarity:.0%} match — {s.title}"):
                    st.write(f"**Root Cause:** {s.root_cause}")
                    st.write(f"**Fix:** {s.recommended_fix}")

    with tabs[1]:
        st.markdown("#### Root Cause")
        st.info(rca.root_cause)
        st.markdown("#### Evidence Summary")
        st.write(rca.evidence_summary)

    with tabs[2]:
        st.markdown("#### Recommended Fix")
        # Format step-by-step if numbered
        fix_text = rca.recommended_fix
        st.markdown(fix_text)

    with tabs[3]:
        st.markdown("#### Validation Steps")
        st.markdown(rca.validation_steps or "_No steps provided._")

    with tabs[4]:
        st.markdown("#### Preventive Actions")
        st.markdown(rca.preventive_actions or "_No actions provided._")

    with tabs[5]:
        st.markdown("#### Download Reports")
        md_path = state.get("report_markdown_path")
        pdf_path = state.get("report_pdf_path")

        if md_path and Path(md_path).exists():
            with open(md_path, "rb") as f:
                st.download_button(
                    "📄 Download Markdown Report",
                    data=f,
                    file_name=Path(md_path).name,
                    mime="text/markdown",
                    use_container_width=True,
                )
        if pdf_path and Path(pdf_path).exists():
            with open(pdf_path, "rb") as f:
                st.download_button(
                    "📑 Download PDF Report",
                    data=f,
                    file_name=Path(pdf_path).name,
                    mime="application/pdf",
                    use_container_width=True,
                )


# ──────────────────────────────────────────────────────────────
# Page: Cluster Dashboard
# ──────────────────────────────────────────────────────────────

def page_dashboard() -> None:
    st.title("☸️ Cluster Dashboard")

    st.button("🔄 Refresh")

    try:
        from app.tools.kubernetes_tools import cluster_health_summary, list_all_pods
        health = cluster_health_summary()
        all_pods = list_all_pods()

        total_pods = len(all_pods)
        healthy_count = sum(1 for p in all_pods if p["Status"].startswith("✅"))
        unhealthy_count = total_pods - healthy_count

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Nodes", health["total_nodes"])
        col2.metric(
            "Ready Nodes",
            health["ready_nodes"],
            delta=health["ready_nodes"] - health["total_nodes"],
            delta_color="normal",
        )
        col3.metric("Total Pods", total_pods)
        col4.metric("Unhealthy Pods", unhealthy_count, delta_color="inverse")

        if health["issue_breakdown"]:
            st.markdown("### Issue Breakdown")
            cols = st.columns(min(len(health["issue_breakdown"]), 4))
            for i, (issue, count) in enumerate(health["issue_breakdown"].items()):
                issue_str = issue.value if hasattr(issue, "value") else str(issue)
                cols[i % 4].metric(issue_str, count)

        st.markdown("### All Pods")
        if all_pods:
            st.dataframe(all_pods, use_container_width=True)
        else:
            st.info("No pods found.")

        st.markdown("### Node Status")
        st.dataframe(health["nodes"], use_container_width=True)

    except Exception as exc:
        st.error(f"Could not connect to Kubernetes: {exc}")
        st.info("Make sure kubectl is configured and the cluster is accessible.")

    # ── Latest RCA Result ──────────────────────────────────────
    st.divider()
    st.markdown("## 🔬 Latest RCA Result")

    last = st.session_state.get("last_rca")
    if last and last.get("rca_output"):
        rca = last["rca_output"]

        sev_color = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(
            rca.severity, "⚪"
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Issue Type", rca.issue_type)
        c2.metric("Severity", f"{sev_color} {rca.severity.upper()}")
        c3.metric("Confidence", f"{rca.confidence_score:.0%}")
        c4.metric("Namespace", last.get("namespace", "—"))

        tabs = st.tabs(["📋 Summary", "🔍 Root Cause", "🛠️ Fix", "✅ Validate", "📥 Download"])

        with tabs[0]:
            st.info(rca.executive_summary)
            if rca.contributing_factors:
                st.markdown("**Contributing Factors**")
                st.write(rca.contributing_factors)
            if rca.impact_assessment:
                st.markdown("**Impact**")
                st.write(rca.impact_assessment)
            if rca.evidence_summary:
                st.markdown("**Evidence**")
                st.write(rca.evidence_summary)

        with tabs[1]:
            st.error(rca.root_cause)

        with tabs[2]:
            st.markdown(rca.recommended_fix)

        with tabs[3]:
            st.markdown(rca.validation_steps or "_No steps provided._")
            st.markdown("**Preventive Actions**")
            st.markdown(rca.preventive_actions or "_None._")

        with tabs[4]:
            md_path = last.get("report_markdown_path")
            pdf_path = last.get("report_pdf_path")
            if md_path and Path(md_path).exists():
                with open(md_path, "rb") as f:
                    st.download_button(
                        "📄 Download Markdown",
                        data=f,
                        file_name=Path(md_path).name,
                        mime="text/markdown",
                        use_container_width=True,
                        key="dash_dl_md",
                    )
            if pdf_path and Path(pdf_path).exists():
                with open(pdf_path, "rb") as f:
                    st.download_button(
                        "📑 Download PDF",
                        data=f,
                        file_name=Path(pdf_path).name,
                        mime="application/pdf",
                        use_container_width=True,
                        key="dash_dl_pdf",
                    )
    else:
        st.info("No RCA has been run yet. Use the **🔍 RCA Analysis** page or the query box to analyse a pod.")

    # ── Recent Incidents ───────────────────────────────────────
    st.divider()
    st.markdown("## 📚 Recent Incidents")
    try:
        from app.database.sqlite_store import SyncIncidentStore
        incidents = SyncIncidentStore().list_recent(limit=5)
        if incidents:
            rows = []
            for inc in incidents:
                sev_icon = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(
                    inc.severity, "⚪"
                )
                rows.append({
                    "Severity": f"{sev_icon} {inc.severity}",
                    "Issue Type": inc.issue_type,
                    "Resource": f"{inc.namespace}/{inc.resource_name}",
                    "Time": inc.created_at.strftime("%Y-%m-%d %H:%M") if inc.created_at else "—",
                    "Confidence": f"{int((inc.confidence_score or 0) * 100)}%",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("No incidents recorded yet.")
    except Exception as exc:
        st.warning(f"Could not load incidents: {exc}")


# ──────────────────────────────────────────────────────────────
# Page: Incident History
# ──────────────────────────────────────────────────────────────

def page_history() -> None:
    st.title("📚 Incident History")

    try:
        from app.database.sqlite_store import SyncIncidentStore
        store = SyncIncidentStore()
        incidents = store.list_recent(limit=30)

        if not incidents:
            st.info("No incidents recorded yet. Run your first analysis to get started.")
            return

        st.markdown(f"### Last {len(incidents)} incidents")

        for inc in incidents:
            severity_icon = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(
                inc.severity, "⚪"
            )
            ts = inc.created_at.strftime("%Y-%m-%d %H:%M UTC") if inc.created_at else "unknown"
            label = (
                f"{severity_icon} [{inc.issue_type}] {inc.namespace}/{inc.resource_name} — {ts}"
            )
            with st.expander(label):
                col1, col2, col3 = st.columns(3)
                col1.write(f"**Status**: {inc.status}")
                col2.write(f"**Confidence**: {inc.confidence_score:.0%}" if inc.confidence_score else "N/A")
                col3.write(f"**Model**: {inc.model_used or 'N/A'}")

                report = store.get_report(inc.id)
                if report:
                    st.markdown("**Executive Summary:**")
                    st.write(report.executive_summary)
                    st.markdown("**Root Cause:**")
                    st.write(report.root_cause)
                    st.markdown("**Fix:**")
                    st.write(report.recommended_fix)

                    if report.markdown_path and Path(report.markdown_path).exists():
                        with open(report.markdown_path, "rb") as f:
                            st.download_button(
                                "📄 Download MD",
                                data=f,
                                file_name=Path(report.markdown_path).name,
                                mime="text/markdown",
                                key=f"md_{inc.id}",
                            )
    except Exception as exc:
        st.error(f"Could not load incident history: {exc}")


# ──────────────────────────────────────────────────────────────
# Page: RCA Reports
# ──────────────────────────────────────────────────────────────

def page_reports() -> None:
    st.title("📊 RCA Reports")

    reports_dir = Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Load incident metadata from DB for enrichment
    inc_meta: dict[str, object] = {}
    try:
        from app.database.sqlite_store import SyncIncidentStore
        for inc in SyncIncidentStore().list_recent(limit=100):
            inc_meta[inc.id] = inc
    except Exception:
        pass

    md_files = sorted(reports_dir.glob("*.md"), reverse=True)

    if not md_files:
        st.info("No RCA reports yet. Run an analysis from the **🔍 RCA Analysis** page to generate one.")
        return

    st.markdown(f"**{len(md_files)} report(s) found**")
    st.divider()

    for md_path in md_files:
        # Filename format: RCA_<short_id>_<timestamp>.md
        stem = md_path.stem                         # e.g. RCA_23abe7c1_20260710T185007Z
        parts = stem.split("_")
        short_id = parts[1] if len(parts) >= 2 else "—"
        ts_raw   = parts[2] if len(parts) >= 3 else ""

        # Parse timestamp
        try:
            from datetime import datetime
            ts = datetime.strptime(ts_raw, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            ts = ts_raw

        # Look up full incident metadata
        inc = next(
            (v for k, v in inc_meta.items() if short_id in k),
            None,
        )

        sev_icon = "⚪"
        sev_label = "—"
        issue_type = "—"
        confidence = "—"
        namespace = "—"
        if inc:
            sev_icon = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(
                getattr(inc, "severity", ""), "⚪"
            )
            sev_label = getattr(inc, "severity", "—").upper()
            issue_type = getattr(inc, "issue_type", "—")
            confidence = f"{int((getattr(inc, 'confidence_score', 0) or 0) * 100)}%"
            namespace = getattr(inc, "namespace", "—")

        label = f"{sev_icon} [{issue_type}] {namespace} — {ts}"
        with st.expander(label, expanded=False):
            # Metadata row
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Severity", f"{sev_label}")
            c2.metric("Issue Type", issue_type)
            c3.metric("Confidence", confidence)
            c4.metric("Namespace", namespace)

            # Report tabs
            tab_view, tab_dl = st.tabs(["📄 View Report", "⬇️ Download"])

            with tab_view:
                content = md_path.read_text()
                st.markdown(content)

            with tab_dl:
                col_md, col_pdf = st.columns(2)
                with col_md:
                    with open(md_path, "rb") as f:
                        st.download_button(
                            "📄 Markdown",
                            data=f,
                            file_name=md_path.name,
                            mime="text/markdown",
                            use_container_width=True,
                            key=f"dl_md_{short_id}",
                        )
                pdf_path = md_path.with_suffix(".pdf")
                with col_pdf:
                    if pdf_path.exists():
                        with open(pdf_path, "rb") as f:
                            st.download_button(
                                "📑 PDF",
                                data=f,
                                file_name=pdf_path.name,
                                mime="application/pdf",
                                use_container_width=True,
                                key=f"dl_pdf_{short_id}",
                            )
                    else:
                        st.caption("PDF not available")


# ──────────────────────────────────────────────────────────────
# Login / Signup page
# ──────────────────────────────────────────────────────────────

def page_login() -> None:
    from app.auth.user_store import authenticate, create_user

    _team_img = Path(__file__).parent.parent.parent / "data" / "assets" / "team.png"

    # Centered layout: empty | card | empty
    _, center, _ = st.columns([1, 2, 1])
    with center:
        if _team_img.exists():
            st.image(str(_team_img), use_container_width=True)

        st.markdown("""
        <div class="auth-logo">🔍</div>
        <div class="auth-title">gvreddy's SRE Agent</div>
        <div class="auth-sub">AI-powered Kubernetes Root Cause Analysis</div>
        """, unsafe_allow_html=True)

        tab_in, tab_up = st.tabs(["Sign In", "Sign Up"])

        # ── Sign In ───────────────────────────────────────────
        with tab_in:
            with st.form("signin_form"):
                st.markdown("#### Welcome back")
                identifier = st.text_input("Username or Email", placeholder="you@example.com or username")
                password   = st.text_input("Password", type="password", placeholder="••••••••")
                submitted  = st.form_submit_button("Sign In", use_container_width=True, type="primary")

            if submitted:
                if not identifier or not password:
                    st.error("Please fill in all fields.")
                else:
                    user = authenticate(identifier, password)
                    if user:
                        st.session_state.authenticated = True
                        st.session_state.current_user  = user
                        st.success(f"Welcome back, **{user.full_name}**! 👋")
                        st.rerun()
                    else:
                        st.error("Invalid username/email or password.")

        # ── Sign Up ───────────────────────────────────────────
        with tab_up:
            with st.form("signup_form"):
                st.markdown("#### Create an account")
                full_name = st.text_input("Full Name", placeholder="Gopala Venkata Reddy")
                username  = st.text_input("Username", placeholder="gvreddy")
                email     = st.text_input("Email", placeholder="you@example.com")
                pw1       = st.text_input("Password", type="password", placeholder="Min 6 characters")
                pw2       = st.text_input("Confirm Password", type="password", placeholder="Repeat password")
                role      = st.selectbox("Role", ["viewer", "operator", "admin"],
                                         help="viewer=read-only, operator=can execute actions, admin=full access")
                submitted = st.form_submit_button("Create Account", use_container_width=True, type="primary")

            if submitted:
                if not all([full_name, username, email, pw1, pw2]):
                    st.error("Please fill in all fields.")
                elif pw1 != pw2:
                    st.error("Passwords do not match.")
                else:
                    ok, msg = create_user(username, email, full_name, pw1, role)
                    if ok:
                        st.success(f"{msg} You can now sign in.")
                    else:
                        st.error(msg)


# ──────────────────────────────────────────────────────────────
# Main Navigation
# ──────────────────────────────────────────────────────────────

def main() -> None:
    namespace, model = render_sidebar()

    pages = {
        "🔍 RCA Analysis": "analysis",
        "☸️ Cluster Dashboard": "dashboard",
        "📚 Incident History": "history",
        "📊 RCA Reports": "reports",
    }

    with st.sidebar:
        st.divider()
        st.markdown("### 📄 Health Reports")
        page_choice = st.radio(
            "Navigate",
            options=list(pages.keys()),
            label_visibility="collapsed",
            key="page_nav",
        )

    page_key = pages[page_choice]
    if page_key == "analysis":
        page_analysis(namespace=namespace, model=model)
    elif page_key == "dashboard":
        page_dashboard()
    elif page_key == "history":
        page_history()
    elif page_key == "reports":
        page_reports()


if not st.session_state.get("authenticated"):
    page_login()
else:
    main()

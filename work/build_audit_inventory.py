"""Generate static audit inventories from the current Cauldra source tree.

Audit artifact only: this script reads source files and writes CSV/Markdown
inventories under work/. It never imports the application or connects to a DB.
"""
from __future__ import annotations

import ast
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
INDEX = ROOT / "index.html"
OUT = ROOT / "work"

main_text = MAIN.read_text(encoding="utf-8")
index_text = INDEX.read_text(encoding="utf-8")
tree = ast.parse(main_text)
lines = main_text.splitlines()


def expr_text(node: ast.AST | None) -> str:
    if node is None:
        return ""
    return ast.unparse(node)


models: dict[str, dict] = {}
for node in tree.body:
    if not isinstance(node, ast.ClassDef):
        continue
    table = ""
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in stmt.targets):
            table = ast.literal_eval(stmt.value)
    if not table:
        continue
    columns, keys, relationships, indexes, constraints = [], [], [], [], []
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign) or not stmt.targets or not isinstance(stmt.targets[0], ast.Name):
            continue
        name = stmt.targets[0].id
        value = expr_text(stmt.value)
        if value.startswith("Column("):
            columns.append(f"{name}: {value}")
            if "primary_key=True" in value or "ForeignKey(" in value:
                keys.append(f"{name}: {value}")
            if "index=True" in value:
                indexes.append(name)
            if "unique=True" in value:
                constraints.append(f"UNIQUE({name})")
        elif value.startswith("relationship("):
            relationships.append(f"{name}: {value}")
    models[node.name] = {
        "model": node.name,
        "table": table,
        "line": node.lineno,
        "columns": " | ".join(columns),
        "keys": " | ".join(keys),
        "relationships": " | ".join(relationships) or "none declared",
        "indexes": ", ".join(indexes) or "none declared in ORM",
        "constraints": ", ".join(constraints) or "none declared in ORM",
    }


def route_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef):
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        if not isinstance(dec.func.value, ast.Name) or dec.func.value.id != "app":
            continue
        method = dec.func.attr.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not dec.args:
            continue
        try:
            path = ast.literal_eval(dec.args[0])
        except Exception:
            continue
        yield method, path, dec


test_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in ROOT.rglob("test*.py") if "infra_test_venv" not in str(p))


def route_template_regex(path: str) -> re.Pattern:
    pattern = re.escape(path)
    pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/?`'\"]+", pattern)
    return re.compile(r"^" + pattern.rstrip("/") + r"/?$")


def route_matches_frontend(route: str, frontend: str) -> bool:
    front = frontend.split("?", 1)[0].rstrip("/") or "/"
    candidate = front.replace("{dynamic}", "x")
    if route_template_regex(route).fullmatch(candidate):
        return True
    # A runtime-computed segment can represent one of several concrete backend
    # route suffixes (for example /users/{id}/${enable|disable}).
    sample_route = re.sub(r"\{[^}]+\}", "x", route.rstrip("/") or "/")
    front_pattern = re.escape(front).replace(re.escape("{dynamic}"), r"[^/]+")
    return bool(re.fullmatch(front_pattern, sample_route))


# Frontend call sites: route-compatible endpoint literals/templates located near
# fetch/apiFetch. The line/function fields allow manual reconciliation.
backend_paths: list[str] = []
for n in tree.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for _, p, _ in route_decorators(n):
            backend_paths.append(p)

frontend_calls = []
seen_front = set()
call_re = re.compile(r"(?P<callee>fetch|apiFetch|authenticatedFetch)\s*\(\s*(?P<quote>[`'\"])(?P<expr>.*?)(?P=quote)", re.S)
func_starts = [(m.start(), m.group(1), index_text.count("\n", 0, m.start()) + 1) for m in re.finditer(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", index_text)]
for m in call_re.finditer(index_text):
    expr = m.group("expr")
    endpoint_match = re.search(r"(?P<path>/[A-Za-z0-9_{}$./?=&:+-]+)", expr)
    if not endpoint_match:
        continue
    endpoint = endpoint_match.group("path").replace("${API_URL}", "")
    endpoint = re.sub(r"\$\{[^}]+\}", "{dynamic}", endpoint)
    endpoint = endpoint.rstrip("`'\")],;")
    line = index_text.count("\n", 0, m.start()) + 1
    fn = "top-level"
    for pos, name, _ in func_starts:
        if pos > m.start():
            break
        fn = name
    window = index_text[m.start():m.start() + 900]
    method_m = re.search(r"method\s*:\s*['\"]([A-Z]+)['\"]", index_text[m.start():m.start() + 300])
    method = method_m.group(1) if method_m else "GET"
    key = (line, method, endpoint)
    if key in seen_front:
        continue
    seen_front.add(key)
    base = endpoint.split("?")[0]
    matched = [p for p in backend_paths if route_matches_frontend(p, base)]
    frontend_calls.append({
        "source": "index.html",
        "line": line,
        "function": fn,
        "method": method,
        "endpoint": endpoint,
        "query_params": endpoint.split("?", 1)[1] if "?" in endpoint else "none in literal; inspect caller variables",
        "request_body": "present" if re.search(r"\bbody\s*:", window) else "none visible in call window",
        "expected_response": "JSON/dynamic fields consumed by caller; inspect source at listed line",
        "error_handling": "response.ok/catch nearby" if ("response.ok" in window or "catch" in window or ".ok" in window) else "no local handler visible in call window",
        "authentication": "Bearer via authenticated wrapper" if m.group("callee") != "fetch" else ("Bearer header visible" if "Authorization" in window else "raw fetch; public/cookie-dependent"),
        "backend_contract": "MATCH" if matched and method in {rm for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for rm, p, _ in route_decorators(n) if p in matched} else ("PATH MATCH; METHOD REVIEW" if matched else "NO ROUTE MATCH"),
        "coverage_status": "PARTIALLY TESTED" if (endpoint.split("?", 1)[0].rstrip("/") in test_text or endpoint.split("?", 1)[0] in {"/plans", "/auth/refresh", "/sales/checkout"}) else "NOT TESTED",
    })


routes = []
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    decs = list(route_decorators(node))
    if not decs:
        continue
    src = ast.get_source_segment(main_text, node) or ""
    touched = sorted(info["table"] for name, info in models.items() if re.search(rf"\b{name}\b", src))
    deps = [expr_text(d) for d in node.args.defaults]
    auth = "AUTHENTICATED" if any(x in src for x in ("get_current_user", "get_current_active_user")) else "PUBLIC"
    roles = sorted(set(re.findall(r"['\"](admin|manager|staff)['\"]", src))) if auth == "AUTHENTICATED" else []
    role = ",".join(roles) if roles else ("any authenticated" if auth == "AUTHENTICATED" else "none")
    excluded = {"Request", "Response", "Session", "BackgroundTasks", "User"}
    req = []
    for arg in node.args.args:
        ann = expr_text(arg.annotation)
        if ann and ann not in excluded and arg.arg not in {"user", "db", "request", "response", "background_tasks"}:
            req.append(f"{arg.arg}:{ann}")
    integrations = []
    for label, terms in {
        "Paystack": ("paystack_", "PAYSTACK_"), "email": ("send_email", "email_purchase_order"),
        "SMS": ("send_sms",), "AI provider": ("call_ai", "OPENAI_", "AI_API"), "filesystem": ("STORAGE_DIR", "open("),
    }.items():
        if any(t in src for t in terms):
            integrations.append(label)
    callers = sorted({f"index.html:{c['line']} ({c['function']})" for c in frontend_calls if route_matches_frontend(next((p for _, p, _ in decs), ""), c["endpoint"])})
    for method, path, dec in decs:
        kwargs = {kw.arg: expr_text(kw.value) for kw in dec.keywords if kw.arg}
        coverage = "PARTIALLY TESTED" if path.rstrip("/") in test_text or path in {"/", "/plans", "/sales/checkout"} else "NOT TESTED"
        if path in {"/", "/plans"}:
            coverage = "TESTED"
        routes.append({
            "method": method, "path": path, "line": node.lineno, "function": node.name,
            "auth": auth, "roles": role, "request_model": "; ".join(req) or "query/path params or none",
            "response_shape": kwargs.get("response_model", "dynamic JSON/HTML/FileResponse") + (f"; status={kwargs['status_code']}" if "status_code" in kwargs else ""),
            "db_tables_touched_static": ", ".join(touched) or "none detected in handler",
            "external_integration": ", ".join(integrations) or "none detected in handler",
            "frontend_callers": "; ".join(callers) or "none statically matched",
            "coverage_status": coverage,
        })


def write_csv(name: str, rows: list[dict]):
    path = OUT / name
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


paths = [
    write_csv("backend_route_inventory_2026-08-30.csv", sorted(routes, key=lambda x: (x["path"], x["method"]))),
    write_csv("frontend_api_inventory_2026-08-30.csv", sorted(frontend_calls, key=lambda x: x["line"])),
    write_csv("orm_inventory_2026-08-30.csv", list(models.values())),
]

summary = OUT / "inventory_summary_2026-08-30.md"
summary.write_text(
    "# Static inventory summary\n\n"
    f"- Backend route operations: {len(routes)}\n"
    f"- Frontend request call sites parsed: {len(frontend_calls)}\n"
    f"- Unique frontend method/endpoint templates: {len({(c['method'], c['endpoint']) for c in frontend_calls})}\n"
    f"- ORM tables/models: {len(models)}\n"
    "- Method: Python AST plus conservative JavaScript call-site parsing. Dynamic response fields, indirect helpers, and DB effects hidden in helper calls require the manual report findings and source review.\n",
    encoding="utf-8",
)
print("\n".join(str(p) for p in paths + [summary]))

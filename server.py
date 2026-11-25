# -*- coding: utf-8 -*-
"""WorkBuddy 个人工作台 - 本地后端服务（Python 标准库，无第三方依赖）"""
import os, json, shutil, datetime, glob, re, sys, subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

# pythonw.exe 无控制台：sys.stdout / sys.stderr 为 None，重定向到空设备避免写入异常
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
WORKBUDDY_ROOT = os.path.dirname(WORKSPACE)


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def discover_personal_dir():
    """自动定位当前用户的 WorkBuddy 个人资料目录，不依赖写死的用户 ID。"""
    override = os.environ.get("WB_PERSONAL_DIR", "").strip()
    if override and os.path.isdir(override):
        return override
    base = os.path.join(HOME, ".workbuddy")
    try:
        names = os.listdir(base)
    except OSError:
        names = []
    candidates = [os.path.join(base, n) for n in names
                  if n.startswith("user-") and n.endswith("-personal")
                  and os.path.isdir(os.path.join(base, n))]
    # 优先包含 MEMORY.md 的目录，其次取最近修改的
    candidates.sort(key=lambda p: (os.path.isfile(os.path.join(p, "MEMORY.md")), _mtime(p)), reverse=True)
    return candidates[0] if candidates else os.path.join(base, "user-unknown-personal")


def discover_wb_catalog():
    """自动定位 WorkBuddy 主题目录的 swr.json，兼容不同用户与不同 scoped ID。"""
    override = os.environ.get("WB_CATALOG", "").strip()
    if override and os.path.isfile(override):
        return override
    storage = os.path.join(HOME, ".workbuddy", "storage")
    hits = glob.glob(os.path.join(storage, "*", "scoped", "*", "swr.json"))
    if not hits:
        hits = glob.glob(os.path.join(storage, "**", "swr.json"), recursive=True)
    hits.sort(key=_mtime, reverse=True)
    return hits[0] if hits else os.path.join(storage, "swr.json")


PERSONAL_DIR = discover_personal_dir()
WB_CATALOG_PATH = discover_wb_catalog()
USER_SKILLS = os.path.join(HOME, ".workbuddy", "skills")
PROJECT_SKILLS = os.path.join(WORKSPACE, ".workbuddy", "skills")
MEMORY_DIR = os.path.join(WORKSPACE, ".workbuddy", "memory")
USER_MEMORY = os.path.join(PERSONAL_DIR, "MEMORY.md")
CONFIG_PATH = os.path.join(HERE, "config.json")
KNOWLEDGE_DIR = os.path.join(WORKSPACE, "knowledge")
KNOWLEDGE_INDEX_PATH = os.path.join(HERE, "knowledge-index.json")
KNOWLEDGE_EXT = {".md", ".markdown", ".txt", ".json", ".csv", ".html", ".htm", ".xml"}
AUTOMATIONS_PATH = os.path.join(HERE, "automations.json")
CONNECTORS_PATH = os.path.join(HERE, "connectors.json")
APPEARANCE_DIR = os.path.join(HOME, ".workbuddy", "appearance-resources")
PORT = 8765

PROTECTED_NAMES = {".workbuddy", "workbench", ".git", "__pycache__", "node_modules"}


def norm(p):
    return os.path.normcase(os.path.realpath(p))


def is_within(path, root):
    try:
        p, r = norm(path), norm(root)
        return p == r or p.startswith(r + os.sep)
    except Exception:
        return False


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    cfg = load_json(CONFIG_PATH, {})
    cfg.setdefault("trash_path", "")
    cfg.setdefault("retention", "30")
    cfg.setdefault("trash_items", [])
    cfg.setdefault("knowledge_path", KNOWLEDGE_DIR)
    return cfg


def save_config(cfg):
    save_json(CONFIG_PATH, cfg)


def trash_valid(cfg):
    p = cfg.get("trash_path", "")
    return bool(p) and os.path.isdir(p)


def list_projects():
    items = []
    try:
        for name in sorted(os.listdir(WORKBUDDY_ROOT)):
            p = os.path.join(WORKBUDDY_ROOT, name)
            if not os.path.isdir(p) or name in PROTECTED_NAMES:
                continue
            try:
                st = os.stat(p)
                mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
                file_count = sum(len(files) for _, _, files in os.walk(p))
            except Exception:
                mtime, file_count = "—", 0
            items.append({"name": name, "path": p, "kind": "项目目录",
                          "modified": mtime, "files": file_count})
    except Exception as e:
        items.append({"name": "读取失败", "path": str(e), "kind": "", "modified": "—", "files": 0})
    return items


def list_files(directory):
    files, dirs = [], []
    try:
        for name in sorted(os.listdir(directory)):
            p = os.path.join(directory, name)
            if name in PROTECTED_NAMES:
                continue
            st = os.stat(p)
            mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            entry = {"name": name, "path": p,
                     "type": "目录" if os.path.isdir(p) else "文件",
                     "size": "—" if os.path.isdir(p) else str(st.st_size) + " B",
                     "modified": mtime}
            (dirs if os.path.isdir(p) else files).append(entry)
    except Exception:
        pass
    return dirs + files


def parse_skill_md(path):
    """解析 SKILL.md，返回 (title, desc, meta字典)。meta 含 domain/author/role/triggers/related 等。"""
    title, desc = "", ""
    meta = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read(8000)
        parts = text.split("---")
        fm = parts[1] if len(parts) > 2 and text.lstrip().startswith("---") else text
        m = re.search(r"^(?:title|display_name):[ \t]*(.+)$", fm, re.M)
        if m:
            title = m.group(1).strip().strip('"\'')
        m = re.search(r"^description:[ \t]*(.+)$", fm, re.M)
        if m:
            desc = m.group(1).strip().strip('"\'')
        m = re.search(r"^name:[ \t]*(.+)$", fm, re.M)
        if m and not title:
            title = m.group(1).strip().strip('"\'')
        for key in ("domain", "author", "version", "role", "license", "triggers", "related-skills"):
            mm = re.search(r"^[ \t]{0,2}" + re.escape(key) + r":[ \t]*(.+)$", fm, re.M)
            if mm:
                val = mm.group(1).strip().strip('"\'')
                meta[key] = [x.strip() for x in val.split(",") if x.strip()] if key in ("triggers", "related-skills") else val
    except Exception:
        pass
    return title, desc, meta


def skill_group(meta, level):
    """按来源归类：Blender 技能集 / 市场安装 / 其他。依据为 SKILL.md 元数据与安装标记。"""
    if level == "项目级":
        return ("project", "项目级技能", "工作区 .workbuddy/skills 目录")
    if meta.get("domain", "").lower() == "blender":
        return ("blender", "Blender 技能集", "同源元数据 domain=blender · author=" + meta.get("author", "未知"))
    if meta.get("_marketplace"):
        return ("marketplace", "市场安装技能", "含 skillhub 安装标记 _skillhub_meta.json")
    return ("other", "其他技能", "未归属上述来源") 


def list_skills():
    items = []
    for base, level in ((USER_SKILLS, "用户级"), (PROJECT_SKILLS, "项目级")):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if not os.path.isdir(d):
                continue
            md = os.path.join(d, "SKILL.md")
            if not os.path.isfile(md):
                continue
            title, desc, meta = parse_skill_md(md)
            is_market = os.path.isfile(os.path.join(d, "_skillhub_meta.json"))
            if is_market:
                meta["_marketplace"] = True
            gkey, glabel, gdesc = skill_group(meta, level)
            items.append({"name": name, "title": title or name, "description": desc,
                          "level": level, "path": d, "status": "已启用",
                          "group": gkey, "group_label": glabel, "group_desc": gdesc,
                          "domain": meta.get("domain", ""), "author": meta.get("author", ""),
                          "role": meta.get("role", ""), "version": meta.get("version", ""),
                          "triggers": meta.get("triggers", []),
                          "related": meta.get("related-skills", []),
                          "marketplace": is_market})
    return items


def read_text(path, limit=20000):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(limit)
    except Exception as e:
        return "读取失败：" + str(e)


def list_memory():
    items = []
    items.append({"name": "用户级 MEMORY.md", "path": USER_MEMORY,
                  "scope": "用户级", "writable": True,
                  "content": read_text(USER_MEMORY)})
    if os.path.isdir(MEMORY_DIR):
        for name in sorted(os.listdir(MEMORY_DIR)):
            if name.endswith(".md"):
                p = os.path.join(MEMORY_DIR, name)
                items.append({"name": name, "path": p, "scope": "工作区",
                              "writable": True, "content": read_text(p)})
    return items


def list_profile():
    items = []
    for name in ("USER.md", "IDENTITY.md", "SOUL.md"):
        p = os.path.join(PERSONAL_DIR, name)
        items.append({"name": name, "path": p, "exists": os.path.isfile(p),
                      "content": read_text(p)})
    return items


def trash_entries(cfg):
    out = []
    retention = cfg.get("retention", "30")
    limit_days = None if retention == "never" else int(retention)
    for it in cfg.get("trash_items", []):
        deleted = it.get("deleted_at", "")
        days = 0
        try:
            dt = datetime.datetime.fromisoformat(deleted)
            days = (datetime.datetime.now() - dt).days
        except Exception:
            pass
        status = "ok"
        label = "剩余 " + str(limit_days - days) + " 天" if limit_days is not None else "不提醒"
        if limit_days is not None:
            if days > limit_days:
                status, label = "expired", "已到期 " + str(days - limit_days) + " 天"
            elif days >= limit_days - 7:
                status, label = "warn", "即将到期 " + str(limit_days - days) + " 天"
        out.append({"id": it["id"], "name": it["name"], "path": it["original_path"],
                    "kind": it.get("kind", ""), "deleted_at": deleted,
                    "days": days, "status": status, "label": label})
    return out


def automations_snapshot():
    data = load_json(AUTOMATIONS_PATH, {})
    return data.get("tasks", [])


def connectors_snapshot():
    data = load_json(CONNECTORS_PATH, {})
    return {
        "synced_at": data.get("synced_at", ""),
        "source": data.get("source", ""),
        "connectors": data.get("connectors", []),
        "builtin": data.get("builtin", []),
    }


def knowledge_root(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("knowledge_path") or KNOWLEDGE_DIR


def knowledge_title(text, filename):
    for line in text.splitlines()[:40]:
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            return m.group(1).strip().strip('"\'')
    m = re.search(r"^(?:title|name):[ \t]*(.+)$", text, re.M | re.I)
    if m:
        return m.group(1).strip().strip('"\'')
    return os.path.splitext(filename)[0]


def knowledge_tags(text, rel_path):
    tags = []
    m = re.search(r"^tags:[ \t]*(.+)$", text, re.M | re.I)
    if m:
        tags += [x.strip().strip('"\'') for x in re.split(r"[,，]", m.group(1)) if x.strip()]
    parent = os.path.dirname(rel_path.replace("\\", "/"))
    if parent:
        tags += [x for x in parent.split("/") if x]
    out, seen = [], set()
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= 8:
            break
    return out


def knowledge_summary(text):
    plain = re.sub(r"\A---[\s\S]*?---\s*", "", text, flags=re.M)
    plain = re.sub(r"^#{1,6}\s*", "", plain, flags=re.M)
    plain = re.sub(r"[`*_>\[\]()]", "", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:180]


def empty_knowledge_index(root, error=""):
    idx = {"root": root, "built_at": "", "count": 0, "documents": [], "valid": False}
    if error:
        idx["error"] = error
    return idx


def build_knowledge_index(cfg=None, root=None):
    """扫描本地知识目录，生成可持久化索引。仅索引文本文件，不联网、不触碰云端授权。"""
    cfg = cfg if cfg is not None else load_config()
    root = root or knowledge_root(cfg)
    if os.path.exists(root) and not os.path.isdir(root):
        return empty_knowledge_index(root, "路径不是目录")
    try:
        os.makedirs(root, exist_ok=True)
    except Exception as e:
        return empty_knowledge_index(root, "无法创建目录：" + str(e))
    docs = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SEARCH_SKIP_DIRS]
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in KNOWLEDGE_EXT:
                continue
            full = os.path.join(current, name)
            try:
                text = read_text(full, 20000)
                st = os.stat(full)
            except Exception:
                continue
            rel = os.path.relpath(full, root)
            docs.append({
                "id": rel.replace("\\", "/"),
                "title": knowledge_title(text, name),
                "name": name,
                "rel_path": rel,
                "path": full,
                "summary": knowledge_summary(text),
                "tags": knowledge_tags(text, rel),
                "size": st.st_size,
                "modified": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "text": text,
            })
            if len(docs) >= 500:
                break
        if len(docs) >= 500:
            break
    idx = {"root": root, "built_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "count": len(docs), "documents": docs, "valid": True}
    save_json(KNOWLEDGE_INDEX_PATH, idx)
    return idx


def knowledge_snapshot(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    root = knowledge_root(cfg)
    idx = load_json(KNOWLEDGE_INDEX_PATH, {})
    if not idx or idx.get("root") != root or not os.path.isdir(root):
        idx = build_knowledge_index(cfg, root)
    idx["valid"] = os.path.isdir(root)
    return idx


def first_css_color(text, key):
    """从 skin.css 中提取某个变量第一次出现的十六进制颜色（支持 3/6/8 位）。"""
    for m in re.finditer(re.escape(key) + r"\s*:\s*([^;]+)", text):
        val = m.group(1).strip()
        h = re.search(r"#([0-9a-fA-F]{8})\b", val)
        if h:
            return "#" + h.group(1)[:6]
        h = re.search(r"#([0-9a-fA-F]{6})\b", val)
        if h:
            return "#" + h.group(1)
        h = re.search(r"#([0-9a-fA-F]{3})\b", val)
        if h:
            c = h.group(1)
            return "#" + "".join(ch * 2 for ch in c)
    return ""


SEARCH_SKIP_DIRS = PROTECTED_NAMES | {".venv", "venv", "dist", "build", ".cache", ".pytest_cache"}
SEARCH_TEXT_EXT = {".md", ".txt", ".json", ".py", ".js", ".html", ".css",
                   ".yml", ".yaml", ".toml", ".csv", ".log", ".bat", ".xml"}


def search_snippet(text, query, radius=64):
    """生成命中关键词附近的纯文本摘要。"""
    text = str(text or "").replace("\r", " ")
    q = str(query or "").lower()
    i = text.lower().find(q)
    if i < 0:
        return text[:radius * 2].replace("\n", " ").strip()
    start, end = max(0, i - radius), min(len(text), i + len(q) + radius)
    return ("…" if start else "") + text[start:end].replace("\n", " ").strip() + ("…" if end < len(text) else "")


def search_hit(query, *parts):
    q = str(query or "").lower()
    return any(q in str(p or "").lower() for p in parts)


def search_workspace(query, limit=60):
    """本地全局搜索：项目、文件、Skill、记忆、个人资料、连接器、自动化与回收站。"""
    q = str(query or "").strip()
    if not q:
        return []
    out = []

    def push(type_key, type_label, title, subtitle, view, action="", path="", snippet="", score=10, **extra):
        item = {"type": type_key, "type_label": type_label, "title": str(title),
                "subtitle": str(subtitle or ""), "view": view, "action": action,
                "path": str(path or ""), "snippet": str(snippet or ""), "score": int(score)}
        item.update(extra)
        out.append(item)

    for p in list_projects():
        if search_hit(q, p.get("name"), p.get("path")):
            push("project", "项目", p.get("name"), p.get("path"), "projects", "project",
                 p.get("path"), score=100 if q.lower() in str(p.get("name", "")).lower() else 55)

    for s in list_skills():
        hay = [s.get("name"), s.get("title"), s.get("description"), s.get("group_label"),
               s.get("domain"), s.get("author"), s.get("role"), s.get("triggers"), s.get("related")]
        if search_hit(q, *hay):
            score = 100 if q.lower() in str(s.get("name", "")).lower() else \
                    70 if q.lower() in str(s.get("title", "")).lower() else 40
            push("skill", "Skill", s.get("title") or s.get("name"),
                 (s.get("group_label") or "") + " · " + (s.get("level") or ""),
                 "skills", "skill", s.get("path"), s.get("description", ""), score,
                 name=s.get("name"), level=s.get("level"), group=s.get("group"))

    walked = 0
    for root, dirs, files in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in SEARCH_SKIP_DIRS]
        rel_root = os.path.relpath(root, WORKSPACE)
        if rel_root != "." and rel_root.count(os.sep) >= 4:
            dirs[:] = []
        for name in files:
            if name in PROTECTED_NAMES:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, WORKSPACE)
            name_hit = search_hit(q, name, rel)
            snippet = ""
            if not name_hit:
                ext = os.path.splitext(name)[1].lower()
                if ext in SEARCH_TEXT_EXT:
                    try:
                        with open(full, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read(20000)
                        if search_hit(q, text):
                            snippet = search_snippet(text, q)
                    except Exception:
                        pass
            if name_hit or snippet:
                push("file", "文件", name, rel, "files", "file", full, snippet,
                     100 if name_hit else 25, directory=root)
            walked += 1
        if len(out) >= limit * 4 or walked >= 6000:
            break

    for m in list_memory():
        if search_hit(q, m.get("name"), m.get("path"), m.get("content")):
            snippet = search_snippet(m.get("content"), q) if q.lower() in str(m.get("content", "")).lower() else ""
            push("memory", "记忆", m.get("name"), m.get("path"), "memory", "memory",
                 m.get("path"), snippet, 60 if q.lower() in str(m.get("name", "")).lower() else 20)

    for p in list_profile():
        if search_hit(q, p.get("name"), p.get("content")):
            snippet = search_snippet(p.get("content"), q) if q.lower() in str(p.get("content", "")).lower() else ""
            push("profile", "个人资料", p.get("name"), "个人资料文件", "profile", "profile",
                 p.get("name"), snippet, 55 if q.lower() in str(p.get("name", "")).lower() else 20)

    for d in knowledge_snapshot().get("documents", []):
        hay = [d.get("title"), d.get("name"), d.get("rel_path"), d.get("summary"), d.get("text")]
        if search_hit(q, *hay):
            snippet = search_snippet(d.get("text"), q) if q.lower() in str(d.get("text", "")).lower() else d.get("summary", "")
            score = 85 if q.lower() in str(d.get("title", "")).lower() else \
                    50 if q.lower() in str(d.get("name", "")).lower() else 30
            push("knowledge", "知识库", d.get("title"), d.get("rel_path"), "knowledge", "knowledge",
                 d.get("path"), snippet, score, doc_id=d.get("id"))

    snap = connectors_snapshot()
    for c in list(snap.get("connectors", [])) + list(snap.get("builtin", [])):
        if isinstance(c, dict) and search_hit(q, c.get("name"), c.get("name_zh"), c.get("description"), c.get("status")):
            push("connector", "连接器", c.get("name_zh") or c.get("name"),
                 "会话层快照 · " + str(c.get("status") or c.get("enabled") or "—"),
                 "tools", "", "", score=50)

    for a in automations_snapshot():
        if isinstance(a, dict) and search_hit(q, a.get("name"), a.get("title"), a.get("schedule")):
            push("automation", "定时任务", a.get("name") or a.get("title"),
                 a.get("schedule", ""), "notifications", "", "", score=45)

    for t in trash_entries(load_config()):
        if search_hit(q, t.get("name"), t.get("path"), t.get("kind")):
            push("trash", "回收站", t.get("name"), t.get("path"), "trash", "", t.get("path"), "", 35)

    out.sort(key=lambda x: (-x["score"], x["type_label"], x["title"]))
    return out[:limit]


def wb_theme_catalog():
    """读取主题目录元数据，返回 resourceKey -> 名称/明暗 的映射。"""
    out = {}
    data = load_json(WB_CATALOG_PATH, {})
    for entry in data.values():
        items = entry.get("data", []) if isinstance(entry, dict) else []
        for it in items:
            if isinstance(it, dict) and it.get("kind") == "theme":
                out[it.get("resourceKey", "")] = {
                    "name": it.get("nameZh") or it.get("nameEn") or it.get("resourceKey", ""),
                    "appearance": it.get("appearance", ""),
                    "vip": it.get("vipLevel", ""),
                }
    return out


def list_wb_themes():
    """从本地 appearance-resources 的 skin.css 提取各主题配色。"""
    catalog = wb_theme_catalog()
    out = []
    for d in sorted(glob.glob(os.path.join(APPEARANCE_DIR, "theme-*"))):
        if not os.path.isdir(d):
            continue
        css_path = os.path.join(d, "skin.css")
        if not os.path.isfile(css_path):
            continue
        key = os.path.basename(d).split("-")[0] + "-" + os.path.basename(d).split("-")[1]
        meta = catalog.get(key, {})
        try:
            with open(css_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            continue
        accent = first_css_color(text, "--wb-accent-blue") or first_css_color(text, "--cb-button-primary")
        bg = first_css_color(text, "--cb-bg-primary") or first_css_color(text, "--wb-home-bg-primary")
        text_color = first_css_color(text, "--cb-text-primary") or first_css_color(text, "--cb-colleagues-remote-chip-fg")
        surface = first_css_color(text, "--cb-main-area-background")
        out.append({
            "key": key,
            "name": meta.get("name") or key,
            "appearance": meta.get("appearance", ""),
            "accent": accent,
            "bg": bg,
            "text": text_color,
            "surface": surface,
        })
    return out


def list_directory(directory):
    if not is_within(directory, WORKBUDDY_ROOT) or not os.path.isdir(directory):
        return None
    return list_files(directory)


def build_state():
    cfg = load_config()
    return {
        "workspace": WORKSPACE,
        "config": {"trash_path": cfg.get("trash_path", ""),
                   "retention": cfg.get("retention", "30"),
                   "trash_valid": trash_valid(cfg)},
        "projects": list_projects(),
        "files": list_files(WORKSPACE),
        "skills": list_skills(),
        "memory": list_memory(),
        "profile": list_profile(),
        "trash": trash_entries(cfg),
        "knowledge": knowledge_snapshot(cfg),
        "automations": automations_snapshot(),
        "connectors": connectors_snapshot(),
        "environment": {
            "workbuddy_detected": os.path.isdir(os.path.join(HOME, ".workbuddy")),
            "personal_found": "unknown" not in os.path.basename(PERSONAL_DIR),
            "skills_found": os.path.isdir(USER_SKILLS),
            "catalog_found": os.path.isfile(WB_CATALOG_PATH),
            "python": sys.version.split()[0],
            "workbuddy_root": WORKBUDDY_ROOT,
        },
        "generated_at": datetime.datetime.now().strftime("%H:%M:%S"),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[workbench] " + (fmt % args) + "\n")

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, msg, code=400):
        self.send_json({"ok": False, "error": msg}, code)

    def read_body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.serve_index()
        elif path == "/api/state":
            self.send_json(build_state())
        elif path == "/api/ping":
            self.send_json({"ok": True, "service": "workbench", "port": PORT})
        elif path == "/api/search":
            query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            self.send_json({"ok": True, "query": query, "results": search_workspace(query)})
        elif path == "/api/knowledge":
            self.send_json(knowledge_snapshot())
        elif path == "/api/wb-themes":
            self.send_json({"ok": True, "source": APPEARANCE_DIR, "themes": list_wb_themes()})
        else:
            self.send_error_json("未找到", 404)

    def serve_index(self):
        p = os.path.join(HERE, "index.html")
        if not os.path.isfile(p):
            self.send_error_json("index.html 缺失", 404)
            return
        with open(p, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()
        cfg = load_config()
        try:
            if path == "/api/config":
                self.api_config(cfg, body)
            elif path == "/api/check-path":
                self.send_json({"ok": True, "exists": os.path.isdir(body.get("path", ""))})
            elif path == "/api/delete":
                self.api_delete(cfg, body)
            elif path == "/api/check-restore":
                it = self.find_item(cfg, body.get("id", ""))
                conflict = bool(it and os.path.exists(it["original_path"]))
                self.send_json({"ok": True, "conflict": conflict})
            elif path == "/api/restore":
                self.api_restore(cfg, body)
            elif path == "/api/purge":
                self.api_purge(cfg, body, False)
            elif path == "/api/browse":
                self.api_browse(body)
            elif path == "/api/open-folder":
                self.api_open_folder(body, cfg)
            elif path == "/api/knowledge-rebuild":
                self.send_json({"ok": True, "index": build_knowledge_index(cfg)})
            elif path == "/api/knowledge-config":
                p = body.get("path", "").strip()
                if p and (not os.path.isabs(p) or not os.path.isdir(p)):
                    return self.send_error_json("知识目录不存在，请先创建")
                cfg["knowledge_path"] = p or KNOWLEDGE_DIR
                save_config(cfg)
                self.send_json({"ok": True, "index": build_knowledge_index(cfg)})
            elif path == "/api/skill-detail":
                self.api_skill_detail(body)
            elif path == "/api/skill-delete":
                self.api_skill_delete(cfg, body)
            elif path == "/api/empty-trash":
                self.api_purge(cfg, {}, True)
            elif path == "/api/save-file":
                self.api_save_file(body)
            elif path == "/api/read-file":
                p = body.get("path", "")
                if not self.allowed_write_path(p):
                    return self.send_error_json("路径不在允许范围内")
                if p.startswith("__PROFILE__"):
                    p = os.path.join(PERSONAL_DIR, p[len("__PROFILE__"):])
                self.send_json({"ok": True, "content": read_text(p)})
            else:
                self.send_error_json("未找到", 404)
        except Exception as e:
            self.send_error_json("操作失败：" + str(e))

    def allowed_write_path(self, p):
        if not p:
            return False
        if p.startswith("__PROFILE__"):
            name = p[len("__PROFILE__"):]
            return name in ("USER.md", "IDENTITY.md", "SOUL.md")
        if is_within(p, os.path.join(PERSONAL_DIR, "USER.md")) or \
           is_within(p, os.path.join(PERSONAL_DIR, "IDENTITY.md")) or \
           is_within(p, os.path.join(PERSONAL_DIR, "SOUL.md")) or \
           is_within(p, USER_MEMORY) or \
           is_within(p, MEMORY_DIR) or is_within(p, WORKSPACE):
            base = os.path.basename(p)
            if base in PROTECTED_NAMES and is_within(p, WORKSPACE) and not is_within(p, os.path.join(WORKSPACE, ".workbuddy")):
                return False
            return True
        if is_within(p, knowledge_root()) and os.path.splitext(p)[1].lower() in KNOWLEDGE_EXT:
            return True
        return False

    def api_config(self, cfg, body):
        tp = body.get("trash_path", "").strip()
        if tp and not os.path.isdir(tp):
            return self.send_error_json("回收站目录不存在或已被删除，请重新选择")
        cfg["trash_path"] = tp
        cfg["retention"] = body.get("retention", "30")
        save_config(cfg)
        self.send_json({"ok": True, "config": cfg})

    def api_delete(self, cfg, body):
        if not trash_valid(cfg):
            return self.send_error_json("未指定有效的回收站目录")
        target = body.get("path", "")
        kind = body.get("kind", "文件")
        if not (is_within(target, WORKBUDDY_ROOT)):
            return self.send_error_json("路径不在允许范围内")
        if norm(target) == norm(WORKBUDDY_ROOT) or norm(target) == norm(WORKSPACE):
            return self.send_error_json("该目录受保护，不可删除")
        if not os.path.exists(target):
            return self.send_error_json("目标不存在")
        item_id = datetime.datetime.now().strftime("%Y%m%d%H%M%S") + str(len(cfg["trash_items"]) + 1)
        dest = os.path.join(cfg["trash_path"], item_id)
        shutil.move(target, dest)
        cfg["trash_items"].append({
            "id": item_id, "name": os.path.basename(target), "original_path": target,
            "kind": kind, "deleted_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "stored_at": dest,
        })
        save_config(cfg)
        self.send_json({"ok": True, "message": "已移入回收站", "id": item_id})

    def find_item(self, cfg, item_id):
        for it in cfg.get("trash_items", []):
            if it.get("id") == item_id:
                return it
        return None

    def api_restore(self, cfg, body):
        it = self.find_item(cfg, body.get("id", ""))
        if not it:
            return self.send_error_json("回收站条目不存在")
        src = it.get("stored_at", "")
        if not os.path.exists(src) or not is_within(src, cfg.get("trash_path", "")):
            return self.send_error_json("回收站文件缺失，可能已被外部删除")
        dest = it["original_path"]
        mode = body.get("mode", "rename")
        if os.path.exists(dest):
            if mode == "cancel":
                return self.send_json({"ok": True, "cancelled": True})
            if mode == "overwrite":
                bak = dest + ".replaced_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
                shutil.move(dest, bak)
                cfg["trash_items"].append({
                    "id": bak, "name": os.path.basename(bak), "original_path": dest,
                    "kind": "被覆盖项", "deleted_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "stored_at": bak,
                })
            else:
                root, ext = os.path.splitext(dest)
                dest = root + "_restored_" + datetime.datetime.now().strftime("%H%M%S") + ext
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
        cfg["trash_items"] = [x for x in cfg["trash_items"] if x.get("id") != it["id"]]
        save_config(cfg)
        self.send_json({"ok": True, "message": "已恢复", "path": dest})

    def api_purge(self, cfg, body, all_items):
        if not trash_valid(cfg) and cfg.get("trash_items"):
            return self.send_error_json("回收站目录无效，无法清理")
        targets = cfg["trash_items"] if all_items else [x for x in cfg["trash_items"] if x.get("id") == body.get("id")]
        if not targets:
            return self.send_json({"ok": True, "message": "回收站为空"})
        removed = []
        for it in targets:
            p = it.get("stored_at", "")
            if not is_within(p, cfg.get("trash_path", "")):
                continue
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.isfile(p):
                os.remove(p)
            removed.append(it["id"])
        cfg["trash_items"] = [x for x in cfg["trash_items"] if x.get("id") not in removed]
        save_config(cfg)
        self.send_json({"ok": True, "message": "已彻底清理 " + str(len(removed)) + " 项"})

    def api_browse(self, body):
        directory = body.get("path", "")
        entries = list_directory(directory)
        if entries is None:
            return self.send_error_json("目录不存在或不在允许范围内")
        self.send_json({"ok": True, "path": directory, "entries": entries})

    def api_open_folder(self, body, cfg=None):
        target = body.get("path", "")
        kr = knowledge_root(cfg)
        if not (is_within(target, WORKBUDDY_ROOT) or is_within(target, kr)) or not os.path.isdir(target):
            return self.send_error_json("目录不存在或不在允许范围内")
        try:
            if sys.platform == "win32":
                os.startfile(os.path.realpath(target))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", os.path.realpath(target)])
            else:
                subprocess.Popen(["xdg-open", os.path.realpath(target)])
        except Exception as e:
            return self.send_error_json("打开文件夹失败：" + str(e))
        self.send_json({"ok": True, "message": "已打开文件夹"})

    def api_skill_detail(self, body):
        name = body.get("name", "")
        level = body.get("level", "用户级")
        base = PROJECT_SKILLS if level == "项目级" else USER_SKILLS
        d = os.path.realpath(os.path.join(base, name))
        if os.path.basename(d) != name or not is_within(d, base) or not os.path.isdir(d):
            return self.send_error_json("技能目录不存在")
        md = os.path.join(d, "SKILL.md")
        if not os.path.isfile(md):
            return self.send_error_json("该技能无 SKILL.md")
        title, desc, meta = parse_skill_md(md)
        self.send_json({"ok": True, "name": name, "title": title or name,
                        "description": desc, "content": read_text(md, 20000),
                        "meta": meta, "path": d})

    def api_skill_delete(self, cfg, body):
        names = body.get("names", [])
        if not isinstance(names, list) or not names:
            return self.send_error_json("未选择技能")
        level = body.get("level", "用户级")
        base = PROJECT_SKILLS if level == "项目级" else USER_SKILLS
        if not trash_valid(cfg):
            return self.send_error_json("未指定有效的回收站目录")
        removed = []
        for name in names:
            if not isinstance(name, str) or not name:
                continue
            d = os.path.realpath(os.path.join(base, name))
            if os.path.basename(d) != name or not is_within(d, base) or not os.path.isdir(d):
                continue
            if not os.path.isfile(os.path.join(d, "SKILL.md")):
                continue
            item_id = datetime.datetime.now().strftime("%Y%m%d%H%M%S") + "S" + str(len(cfg["trash_items"]) + len(removed) + 1)
            dest = os.path.join(cfg["trash_path"], item_id)
            shutil.move(d, dest)
            cfg["trash_items"].append({
                "id": item_id, "name": name, "original_path": d,
                "kind": "Skill", "deleted_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "stored_at": dest,
            })
            removed.append(name)
        if removed:
            save_config(cfg)
        self.send_json({"ok": True, "removed": removed,
                        "message": "已移入回收站 %d 个技能" % len(removed)})

    def api_save_file(self, body):
        p = body.get("path", "")
        if not self.allowed_write_path(p):
            return self.send_error_json("路径不在允许范围内")
        if p.startswith("__PROFILE__"):
            p = os.path.join(PERSONAL_DIR, p[len("__PROFILE__"):])
        if os.path.isdir(p):
            return self.send_error_json("目标是目录，不可写入")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body.get("content", ""))
        self.send_json({"ok": True, "message": "已保存"})


if __name__ == "__main__":
    os.chdir(HERE)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("WorkBuddy 工作台已启动: http://127.0.0.1:%d" % PORT)
    print("工作区: " + WORKSPACE)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("已停止")
        srv.server_close()

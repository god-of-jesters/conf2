import argparse
import sys
import os
from pathlib import Path
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from collections import defaultdict
import tkinter as tk
import math

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("-p", "--package", required=True)
    p.add_argument("-r", "--repo", default="https://repo1.maven.org/maven2")
    p.add_argument("-m", "--mode", choices=["remote", "local", "test"], default="remote")
    p.add_argument("-v", "--version", default="")
    p.add_argument("-f", "--filter", default=None)
    p.add_argument("--show-load-order", action="store_true")
    p.add_argument("--d2", action="true", nargs="?")
    return p.parse_args()

def build_pom_path(group_id, artifact_id, version, repo, mode):
    if mode == "remote":
        group_path = group_id.replace(".", "/")
        repo = repo.rstrip("/")
        return f"{repo}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.pom"
    group_path = group_id.replace(".", "/")
    return str(Path(repo) / group_path / artifact_id / version / f"{artifact_id}-{version}.pom")

def fetch_pom_remote(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return resp.read().decode("utf-8")

def fetch_pom_local(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(path))
    return p.read_text(encoding="utf-8")

def parse_pom_direct_deps(pom_xml):
    root = ET.fromstring(pom_xml)
    ns_prefix = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0].strip("{")
        ns_prefix = f"{{{ns}}}"
    deps = []
    dn = root.find(f"./{ns_prefix}dependencies")
    if dn is None:
        return deps
    for d in dn.findall(f"./{ns_prefix}dependency"):
        gid = d.find(f"./{ns_prefix}groupId")
        aid = d.find(f"./{ns_prefix}artifactId")
        ver = d.find(f"./{ns_prefix}version")
        if gid is not None and aid is not None:
            g = gid.text.strip()
            a = aid.text.strip()
            v = ver.text.strip() if ver is not None and ver.text else ""
            if g and a:
                deps.append(f"{g}:{a}" + (f":{v}" if v else ""))
    return deps

def load_test_repo(path):
    out = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            left, right = line.split(":", 1)
            out[left.strip()] = [x.strip() for x in right.split() if x.strip()]
        else:
            out[line.strip()] = []
    return out

def build_transitive_graph(start, mode, repo, version, filter_substr):
    graph = defaultdict(set)
    visited = set()
    in_stack = set()
    cycles = []
    load_order = []
    test_repo_map = load_test_repo(repo) if mode == "test" else {}

    def need_skip(n):
        return filter_substr and filter_substr.lower() in n.lower()

    def deps_of(coord):
        if mode == "test":
            return test_repo_map.get(coord, [])
        if ":" not in coord:
            return []
        p = coord.split(":")
        g, a = p[0], p[1]
        v = p[2] if len(p) >= 3 and p[2] else version
        pom = build_pom_path(g, a, v, repo, "remote" if mode == "remote" else "local")
        try:
            xml = fetch_pom_remote(pom) if mode == "remote" else fetch_pom_local(pom)
            return parse_pom_direct_deps(xml)
        except:
            return []

    if need_skip(start):
        return graph, visited, cycles, load_order

    st = [(start, iter(deps_of(start)))]
    in_stack.add(start)

    while st:
        node, it = st[-1]
        try:
            n = next(it)
            graph[node].add(n)
            if need_skip(n):
                continue
            if n in in_stack:
                path = [x for x, _ in st]
                i = path.index(n)
                cycles.append(path[i:] + [n])
                continue
            if n in visited:
                continue
            in_stack.add(n)
            st.append((n, iter(deps_of(n))))
        except StopIteration:
            st.pop()
            in_stack.discard(node)
            if node not in visited:
                visited.add(node)
                load_order.append(node)
    return graph, visited, cycles, load_order

def graph_to_d2(graph):
    lines = []
    lines.append("direction: right")
    nodes = set(graph.keys())
    for t in graph.values():
        for x in t:
            nodes.add(x)
    for n in sorted(nodes):
        b = n.replace(" (skipped)", "")
        lines.append(f'{b}: {{label: "{b}"}}')
    for s, t in graph.items():
        s2 = s.replace(" (skipped)", "")
        for x in t:
            x2 = x.replace(" (skipped)", "")
            lines.append(f"{s2} -> {x2}")
    return "\n".join(lines)

def show_graph_tk(graph):
    nodes = set(graph.keys())
    for t in graph.values():
        for x in t:
            nodes.add(x)
    nodes = sorted(nodes)
    if not nodes:
        return
    R = 200
    CX, CY = 250, 250
    pos = {}
    for i, n in enumerate(nodes):
        ang = 2 * math.pi * i / len(nodes)
        pos[n] = (CX + R * math.cos(ang), CY + R * math.sin(ang))
    root = tk.Tk()
    c = tk.Canvas(root, width=500, height=500, bg="white")
    c.pack(fill="both", expand=True)
    for s, tg in graph.items():
        x1, y1 = pos[s]
        for t in tg:
            if t not in pos:
                continue
            x2, y2 = pos[t]
            c.create_line(x1, y1, x2, y2)
    r = 15
    for n, (x, y) in pos.items():
        c.create_oval(x - r, y - r, x + r, y + r)
        c.create_text(x, y, text=n)
    root.mainloop()

def main():
    a = parse_args()
    graph, visited, cycles, load = build_transitive_graph(a.package, a.mode, a.repo, a.version, a.filter)
    print("GRAPH:")
    for s, tg in graph.items():
        for t in tg:
            print(f"{s} -> {t}")
    print("VISITED:", *sorted(visited))
    if cycles:
        print("CYCLES:")
        for c in cycles:
            print(" -> ".join(c))
    if a.show_load_order:
        print("LOAD ORDER:")
        for n in load:
            print(n)
    if a.d2:
        print("D2:")
        print(graph_to_d2(graph))

if __name__ == "__main__":
    main()

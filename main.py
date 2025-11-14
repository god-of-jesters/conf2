#!/usr/bin/env python3
"""
Этап 4: дополнительные операции над графом зависимостей.

Добавлено:
- Режим вывода на экран порядка загрузки зависимостей (--show-load-order).
  Порядок считается по постфиксному обходу DFS (итеративному, без рекурсии):
  все зависимости узла идут ДО самого узла.
"""

import argparse
import sys
from pathlib import Path
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from collections import defaultdict

DEFAULT_REPO = "https://repo1.maven.org/maven2"

def parse_args():
    p = argparse.ArgumentParser(
        description="Этап 3+4: граф зависимостей (DFS без рекурсии, циклы, фильтр, порядок загрузки)"
    )
    p.add_argument("-p", "--package", required=True,
                   help="GroupId:ArtifactId (или 'A' в режиме test)")
    p.add_argument("-r", "--repo", default=DEFAULT_REPO,
                   help="URL репозитория / локальный путь / файл тестового репо")
    p.add_argument("-m", "--mode", choices=["remote", "local", "test"], default="remote",
                   help="Режим: remote, local, test")
    p.add_argument("-v", "--version", default="",
                   help="Версия пакета (игнорируется в режиме test)")
    p.add_argument("-f", "--filter", default=None,
                   help="Подстрока для исключения пакетов из анализа (case-insensitive)")
    p.add_argument("--show-load-order", action="store_true",
                   help="Показать порядок загрузки зависимостей (постфиксный DFS)")
    p.add_argument("--d2", action="store_true",
               help="Вывести текстовое описание графа в формате D2")
    return p.parse_args()

# ---------- POM utilities (remote/local) ----------

def build_pom_path(group_id: str, artifact_id: str, version: str, repo: str, mode: str):
    if mode == "remote":
        group_path = group_id.replace(".", "/")
        repo = repo.rstrip("/")
        filename = f"{artifact_id}-{version}.pom"
        return f"{repo}/{group_path}/{artifact_id}/{version}/{filename}"
    else:
        group_path = group_id.replace(".", "/")
        local_path = Path(repo) / group_path / artifact_id / version / f"{artifact_id}-{version}.pom"
        return str(local_path)

def fetch_pom_remote(url: str, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                raise urllib.error.HTTPError(url, resp.status, resp.reason, resp.headers, None)
            return resp.read().decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Ошибка загрузки POM: {e} ({url})")

def fetch_pom_local(path: str):
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"POM не найден: {path}")
    return p.read_text(encoding="utf-8")

def parse_pom_direct_deps(pom_xml: str):
    try:
        root = ET.fromstring(pom_xml)
    except ET.ParseError as e:
        raise RuntimeError(f"XML parse error: {e}")

    ns_prefix = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0].strip("{")
        ns_prefix = f"{{{ns}}}"

    deps = []
    deps_node = root.find(f"./{ns_prefix}dependencies")
    if deps_node is None:
        return deps

    for dep in deps_node.findall(f"./{ns_prefix}dependency"):
        gid = dep.find(f"./{ns_prefix}groupId")
        aid = dep.find(f"./{ns_prefix}artifactId")
        ver = dep.find(f"./{ns_prefix}version")
        gid_text = gid.text.strip() if gid is not None and gid.text else None
        aid_text = aid.text.strip() if aid is not None and aid.text else None
        ver_text = ver.text.strip() if ver is not None and ver.text else None
        if gid_text and aid_text:
            coord = f"{gid_text}:{aid_text}" + (f":{ver_text}" if ver_text else "")
            deps.append(coord)
    return deps

# ---------- test-repo (A,B,C...) ----------

def load_test_repo(path: str):
    mapping = {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Test repo file not found: {path}")
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            left, right = line.split(":", 1)
            key = left.strip()
            deps = [x.strip() for x in right.split() if x.strip()]
            mapping[key] = deps
        else:
            key = line.strip()
            mapping[key] = []
    return mapping

# ---------- core: iterative DFS + load order ----------

def build_transitive_graph(start_coord: str, mode: str, repo: str,
                           version: str, filter_substr: str):
    """
    Возвращает:
      graph: dict(node -> set(neighbors))
      visited: set(all processed nodes)
      cycles: list of cycles (each is list of nodes)
      load_order: list of nodes в порядке загрузки (dependencies-before-dependents)
    """
    graph = defaultdict(set)
    visited = set()
    in_stack = set()
    cycles = []
    load_order = []  # сюда пишем узел, когда он полностью обработан (post-order)

    test_repo_map = {}
    if mode == "test":
        test_repo_map = load_test_repo(repo)

    def should_skip(name: str) -> bool:
        if not filter_substr:
            return False
        return filter_substr.lower() in name.lower()

    def get_direct_deps(coord: str):
        if mode == "test":
            return test_repo_map.get(coord, [])
        if ":" not in coord:
            return []
        parts = coord.split(":")
        group_id = parts[0]
        artifact_id = parts[1]
        ver = parts[2] if len(parts) >= 3 and parts[2] else version
        pom_path = build_pom_path(group_id, artifact_id, ver, repo,
                                  "remote" if mode == "remote" else "local")
        try:
            pom_xml = fetch_pom_remote(pom_path) if mode == "remote" else fetch_pom_local(pom_path)
            return parse_pom_direct_deps(pom_xml)
        except Exception as e:
            print(f"Warning: cannot fetch/parse POM for {coord}: {e}", file=sys.stderr)
            return []

    start = start_coord.strip()
    if should_skip(start):
        print(f"Start node '{start}' совпадает с фильтром — обход не выполняется.")
        return graph, visited, cycles, load_order

    # стек: (node, iterator_over_neighbors)
    stack = []
    stack.append((start, iter(get_direct_deps(start))))
    in_stack.add(start)

    while stack:
        node, nbr_iter = stack[-1]
        try:
            nbr = next(nbr_iter)

            if should_skip(nbr):
                graph[node].add(nbr + " (skipped)")
                continue

            graph[node].add(nbr)

            if nbr in in_stack:
                # цикл
                path_nodes = [n for (n, _) in stack]
                idx = path_nodes.index(nbr)
                cycle_path = path_nodes[idx:] + [nbr]
                cycles.append(cycle_path)
                continue

            if nbr in visited:
                continue

            in_stack.add(nbr)
            stack.append((nbr, iter(get_direct_deps(nbr))))

        except StopIteration:
            # соседи закончились -> узел полностью обработан
            stack.pop()
            in_stack.discard(node)
            if node not in visited:
                visited.add(node)
                load_order.append(node)  # post-order: dependencies уже в load_order раньше

    return graph, visited, cycles, load_order

def graph_to_d2(graph: dict) -> str:
    """
    Преобразует graph (node -> set(neighbors)) в текст D2.
    """
    lines = []
    lines.append("# D2 diagram for dependency graph")
    lines.append("direction: right")  # можно убрать, это просто пример

    # Собираем все узлы (на всякий случай)
    nodes = set(graph.keys())
    for targets in graph.values():
        for t in targets:
            nodes.add(t)

    # Явно объявим узлы (не обязательно, но аккуратнее)
    for n in sorted(nodes):
        # Чистим суффикс (skipped) если используешь его
        base = n.replace(" (skipped)", "")
        lines.append(f'{base}: {{ label: "{base}" }}')

    # Рёбра
    for src, targets in graph.items():
        src_clean = src.replace(" (skipped)", "")
        for t in targets:
            tgt_clean = t.replace(" (skipped)", "")
            lines.append(f"{src_clean} -> {tgt_clean}")

    return "\n".join(lines)

import math
import tkinter as tk

def show_graph_tk(graph: dict):
    """
    Примитивная визуализация графа через Tkinter:
    - узлы по кругу
    - рёбра линиями
    """
    # Собираем узлы
    nodes = set(graph.keys())
    for targets in graph.values():
        for t in targets:
            nodes.add(t)

    nodes = sorted(nodes)
    if not nodes:
        print("Граф пуст, нечего рисовать.")
        return

    # Позиции на окружности
    R = 200
    CX, CY = 250, 250
    positions = {}
    for i, node in enumerate(nodes):
        angle = 2 * math.pi * i / len(nodes)
        x = CX + R * math.cos(angle)
        y = CY + R * math.sin(angle)
        positions[node] = (x, y)

    root = tk.Tk()
    root.title("Dependency graph")
    canvas = tk.Canvas(root, width=500, height=500, bg="white")
    canvas.pack(fill="both", expand=True)

    # Рёбра
    for src, targets in graph.items():
        x1, y1 = positions[src]
        for t in targets:
            x2, y2 = positions[t]
            canvas.create_line(x1, y1, x2, y2)

    # Узлы
    radius = 15
    for node, (x, y) in positions.items():
        canvas.create_oval(x - radius, y - radius, x + radius, y + radius)
        canvas.create_text(x, y, text=node)

    root.mainloop()

# ---------- CLI main ----------

def main():
    args = parse_args()

    settings = {
        "package": args.package,
        "repo": args.repo,
        "mode": args.mode,
        "version": args.version,
        "filter": args.filter,
        "show_load_order": args.show_load_order,
    }
    print("Параметры:")
    for k, v in settings.items():
        print(f"{k}: {v}")
    print("-" * 40)

    if args.mode != "test" and ":" not in args.package:
        print("Ошибка: для remote/local --package должен быть groupId:artifactId", file=sys.stderr)
        sys.exit(2)

    try:
        graph, visited, cycles, load_order = build_transitive_graph(
            args.package, args.mode, args.repo, args.version, args.filter
        )
    except FileNotFoundError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Непредвиденная ошибка: {e}", file=sys.stderr)
        sys.exit(10)

    print("Граф зависимостей (рёбра):")
    if not graph:
        print("(пустой граф)")
    else:
        for src, targets in graph.items():
            for t in targets:
                print(f"{src} -> {t}")
    print("-" * 20)

    print("Посещённые узлы:")
    if visited:
        for v in sorted(visited):
            print(v)
    else:
        print("(нет посещённых узлов)")
    print("-" * 20)

    if cycles:
        print("Обнаруженные циклы:")
        for c in cycles:
            print(" -> ".join(c))
    else:
        print("Циклов не обнаружено.")
    print("-" * 20)

    if args.show_load_order:
        print("Порядок загрузки (dependencies-before-dependents):")
        if not load_order:
            print("(пусто)")
        else:
            # load_order сейчас: [dep1, dep2, ..., root] — уже в нужном порядке
            for n in load_order:
                print(n)
    
    if args.mode == "test":
        show_graph_tk(graph)

if __name__ == "__main__":
    main()

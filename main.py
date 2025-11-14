#!/usr/bin/env python3
"""
maven_graph_cli.py — Этап 3 (пункты 1-3).
- Итеративный DFS (без рекурсии) для построения транзитивного графа зависимостей.
- Фильтрация пакетов по подстроке (игнорировать пакеты, имя которых содержит подстроку).
- Обнаружение циклов и корректная обработка.
- Режим test: читать граф из файла, где пакеты — большие латинские буквы.

Usage examples:
 python maven_graph_cli.py --package org.example:app --version 1.0.0 --repo https://repo1.maven.org/maven2 --mode remote
 python maven_graph_cli.py --mode test --repo ./test_graph.txt --package A --filter C
 python maven_graph_cli.py --mode local --repo /path/to/local/maven/repo --package org.x:lib -v 2.1.0
"""
import argparse
import os
import sys
from pathlib import Path
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from collections import defaultdict, deque

DEFAULT_REPO = "https://repo1.maven.org/maven2"

def parse_args():
    p = argparse.ArgumentParser(description="Этап 3: построение графа зависимостей (итеративный DFS, фильтрация, обработка циклов)")
    p.add_argument("-p", "--package", required=True, help="GroupId:ArtifactId (или 'A' в режиме test)")
    p.add_argument("-r", "--repo", default=DEFAULT_REPO, help="URL репозитория / локальный путь / файл описания тестового репозитория")
    p.add_argument("-m", "--mode", choices=["remote", "local", "test"], default="remote", help="Режим: remote, local, test")
    p.add_argument("-v", "--version", default="", help="Версия пакета (необязательно для test режима)")
    p.add_argument("-f", "--filter", default=None, help="Подстрока для исключения пакетов из анализа (case-insensitive)")
    return p.parse_args()

# -----------------------------
# 1) Утилиты для получения direct deps
# -----------------------------
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
            return resp.read().decode('utf-8')
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
        # normalize coordinate: group:artifact:version (version may be None)
        if gid_text and aid_text:
            coord = f"{gid_text}:{aid_text}" + (f":{ver_text}" if ver_text else "")
            deps.append(coord)
    return deps

# -----------------------------
# 2) Тестовый режим — файл формата:
# A: B C
# B: C D
# C:
# (пакеты — одиночные большие латинские буквы)
# -----------------------------
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
            # строка без ":" — считаем, что нет зависимостей
            key = line.strip()
            mapping[key] = []
    return mapping

# -----------------------------
# 3) Итеративный DFS (без рекурсии), построение транзитивного графа, фильтрация, детекция циклов
# -----------------------------
def build_transitive_graph(start_coord: str, mode: str, repo: str, version: str, filter_substr: str):
    # graph: adjacency list (node -> set(neighbors))
    graph = defaultdict(set)
    visited = set()    # полностью обработанные узлы
    in_stack = set()   # вершины в текущем пути (для обнаружения цикла)
    cycles = []        # список циклов (как списки узлов)
    stack = []         # основной стек для DFS: каждый элемент (node, iterator_over_neighbors)

    def should_skip(node_name: str):
        if not filter_substr:
            return False
        return filter_substr.lower() in node_name.lower()

    # helper to get direct deps for a coordinate
    def get_direct_deps(coord):
        if mode == "test":
            # here coord is single letter like 'A'
            return test_repo_map.get(coord, [])
        # otherwise coord is group:artifact[:version]
        if ":" not in coord:
            return []
        parts = coord.split(":")
        group_id = parts[0]
        artifact_id = parts[1]
        # prefer explicit version, else use provided outer 'version'
        ver = parts[2] if len(parts) >= 3 and parts[2] else version
        pom_path = build_pom_path(group_id, artifact_id, ver, repo, 'remote' if mode == 'remote' else 'local')
        try:
            pom_xml = fetch_pom_remote(pom_path) if mode == "remote" else fetch_pom_local(pom_path)
            deps = parse_pom_direct_deps(pom_xml)
            return deps
        except Exception as e:
            # в минимальном прототипе: лог и возврат пустого списка (не прерываем весь процесс)
            print(f"Warning: cannot fetch/parse POM for {coord}: {e}", file=sys.stderr)
            return []

    # prepare test repo map if needed
    test_repo_map = {}
    if mode == "test":
        test_repo_map = load_test_repo(repo)

    # стартовый узел
    start = start_coord.strip()
    if should_skip(start):
        print(f"Start node '{start}' совпадает с фильтрующей подстрокой — ничего не делаем.")
        return graph, set(), cycles

    # push start
    stack.append((start, iter(get_direct_deps(start))))
    in_stack.add(start)

    while stack:
        node, nbr_iter = stack[-1]
        try:
            nbr = next(nbr_iter)
            if should_skip(nbr):
                # игнорируем зависимость целиком
                # но сохраняем ребро для полноты графа — помечаем как пропущенное (не добавляем в обход)
                graph[node].add(nbr + " (skipped)")
                continue
            # добавляем ребро
            graph[node].add(nbr)
            if nbr in in_stack:
                # найден цикл — выгребаем цикл-путь из stack
                cycle = []
                # собрать путь от первой встречи nbr до конца стека
                for n, _ in stack:
                    cycle.append(n)
                    if n == nbr:
                        break
                # цикл — nbr ... current node ... nbr (закрытый), лучше вывести корректную последовательность
                # делаем цикл корректной ориентации:
                idx = [n for n, _ in stack].index(nbr)
                cycle_path = [n for n, _ in stack][idx:] + [nbr]
                cycles.append(cycle_path)
                # не входим повторно в уже в стеке узел
                continue
            if nbr in visited:
                # уже полностью обработано — просто добавляем ребро
                continue
            # иначе — спускаемся дальше: push neighbor
            in_stack.add(nbr)
            stack.append((nbr, iter(get_direct_deps(nbr))))
        except StopIteration:
            # все соседи обработаны — пометим node как завершённый
            stack.pop()
            in_stack.discard(node)
            visited.add(node)
    return graph, visited, cycles

# -----------------------------
# 4) CLI main
# -----------------------------
def main():
    args = parse_args()
    settings = {
        "package": args.package,
        "repo": args.repo,
        "mode": args.mode,
        "version": args.version,
        "filter": args.filter
    }
    print("Параметры:")
    for k, v in settings.items():
        print(f"{k}: {v}")
    print("-" * 40)

    # Валидация простая:
    if args.mode != "test" and ":" not in args.package:
        print("Ошибка: для режимов remote/local параметр --package должен быть groupId:artifactId", file=sys.stderr)
        sys.exit(2)
    if args.mode == "test" and args.version:
        print("Notice: версия игнорируется в test режиме.", file=sys.stderr)

    try:
        graph, visited, cycles = build_transitive_graph(args.package, args.mode, args.repo, args.version, args.filter)
    except FileNotFoundError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Непредвиденная ошибка: {e}", file=sys.stderr)
        sys.exit(10)

    print("Результат (транзитивный граф):")
    if not graph:
        print("(пустой граф)")
    else:
        for src, targets in graph.items():
            for t in targets:
                print(f"{src} -> {t}")
    print("-" * 20)
    print("Посещённые узлы (транзитивно):")
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

if __name__ == "__main__":
    main()

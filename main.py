import argparse
from pathlib import Path

import requests
from bs4 import BeautifulSoup


def parse_args():
    p = argparse.ArgumentParser(
        description="Порядок загрузки зависимостей (test .txt / remote HTML)"
    )
    p.add_argument(
        "-m", "--mode",
        choices=["test", "remote"],
        required=True,
        help="test = директория с .txt, remote = удалённый репозиторий (base URL)"
    )
    p.add_argument(
        "-r", "--repo",
        required=True,
        help="test: директория с .txt; remote: базовый URL репозитория"
    )
    p.add_argument(
        "-p", "--package",
        required=True,
        help="Корневой пакет, от которого считаем порядок"
    )
    p.add_argument(
        "-v", "--version",
        default="N/A",
        help="Версия пакета (только для вывода)"
    )
    return p.parse_args()


# ======================= PARSERS =======================

def parse_dependencies_from_txt(txt: str):
    """
    configur:
        version: 1.0.0
        dependents:
            python 3.12
            Flask: 2.12
            bs4: 4.21
    -> ['python', 'Flask', 'bs4']
    """
    deps = []
    lines = txt.splitlines()
    in_deps = False
    deps_indent = None

    for line in lines:
        if not in_deps:
            if "dependents:" in line:
                in_deps = True
                deps_indent = len(line) - len(line.lstrip())
            continue

        if in_deps:
            l = line.split(':')
            deps.append(l[0].strip())
    return deps


def parse_dependencies_from_html(html_text: str):
    """
    Парсер для страниц репозитория Alpine (секция 'Depends').
    При необходимости адаптируй под свой формат.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    dependencies = []

    depends_section = soup.find(
        "summary",
        string=lambda text: text and "Depends" in text
    )
    if not depends_section:
        return dependencies

    ul = depends_section.find_next("ul")
    if not ul:
        return dependencies

    for li in ul.find_all("li"):
        dep = li.text.strip()
        if dep.startswith("so:"):
            dep = dep[3:]
            dep = dep.split(".")[0]
        if dep and dep not in dependencies:
            dependencies.append(dep)

    return dependencies


def build_graph_from_txt_dir(dir_path: str) -> dict[str, set[str]]:
    """
    test-режим: берём все *.txt из директории и строим граф:
      <имя файла> -> dependents из файла.
    """
    p = Path(dir_path)
    if not p.is_dir():
        raise ValueError(f"'{dir_path}' не является директорией")

    graph: dict[str, set[str]] = {}

    for txt_file in p.glob("*.txt"):
        pkg_name = txt_file.stem
        text = txt_file.read_text(encoding="utf-8")
        deps = parse_dependencies_from_txt(text)
        graph[pkg_name] = set(deps)

    for deps in list(graph.values()):
        for dep in deps:
            graph.setdefault(dep, set())

    return graph


def fetch_remote_deps(base_url: str, package: str) -> list[str]:
    """
    remote-режим: грузим HTML для package и парсим Depends.
    base_url типа: https://pkgs.alpinelinux.org/package/v3.14/main/x86_64
    """
    base = base_url.rstrip("/")
    url = f"{base}/{package}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return parse_dependencies_from_html(resp.text)


def build_graph_remote(root: str, base_url: str) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    visited: set[str] = set()
    stack: list[str] = [root]

    while stack:
        pkg = stack.pop()
        if pkg in visited:
            continue
        visited.add(pkg)

        try:
            deps = fetch_remote_deps(base_url, pkg)
        except Exception:
            deps = []  # если не смогли скачать/распарсить — считаем без deps

        graph[pkg] = set(deps)

        for d in deps:
            if d not in visited:
                stack.append(d)

    # добиваем вершины без исходящих рёбер
    for deps in list(graph.values()):
        for dep in deps:
            graph.setdefault(dep, set())

    return graph


def compute_load_order(graph: dict[str, set[str]], root: str):
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in graph}
    order: list[str] = []
    cycles: list[tuple[str, str]] = []

    if root not in color:
        return order, cycles

    stack: list[tuple[str, object]] = []
    color[root] = GRAY
    stack.append((root, iter(graph.get(root, []))))

    while stack:
        node, it = stack[-1]
        try:
            nxt = next(it)
        except StopIteration:
            stack.pop()
            color[node] = BLACK
            order.append(node)
            continue

        c = color.get(nxt, WHITE)
        if c == WHITE:
            color[nxt] = GRAY
            stack.append((nxt, iter(graph.get(nxt, []))))
        elif c == GRAY:
            cycles.append((node, nxt))
        # BLACK — уже обработан, игнорируем

    order.reverse()
    return order, cycles


def main():
    args = parse_args()

    if args.mode == "test":
        graph = build_graph_from_txt_dir(args.repo)
    else:  # remote
        graph = build_graph_remote(args.package, args.repo)

    load_order, cycles = compute_load_order(graph, args.package)

    print(f"mode: {args.mode}")
    print(f"repo: {args.repo}")
    print(f"package: {args.package}")
    print(f"version: {args.version}")

    print("\nload order (dependencies load sequence):")
    if load_order:
        for name in load_order:
            print(f"- {name}")
    else:
        print("- (none)")

    print("\ncycles detected:")
    if cycles:
        for u, v in cycles:
            print(f"- {u} -> {v}")
    else:
        print("- none")


if __name__ == "__main__":
    main()

import argparse
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup


from pathlib import Path

def build_graph_from_txt_dir(dir_path: str) -> dict[str, set[str]]:
    p = Path(dir_path)
    if not p.is_dir():
        raise ValueError(f"'{dir_path}' не является директорией")

    graph: dict[str, set[str]] = {}

    # сначала собираем зависимости для всех файлов *.txt
    for txt_file in p.glob("*.txt"):
        pkg_name = txt_file.stem  # без ".txt"
        text = txt_file.read_text(encoding="utf-8")
        deps = parse_dependencies_from_txt(text)
        graph[pkg_name] = set(deps)

    # теперь гарантируем, что все упомянутые deps тоже есть в graph
    for deps in list(graph.values()):
        for dep in deps:
            graph.setdefault(dep, set())

    return graph


def parse_args():
    parser = argparse.ArgumentParser(
        description="Минимальный сборщик зависимостей пакета"
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["remote", "test"],
        required=True,
        help="Режим работы: remote (URL) или test (локальный файл/директория)"
    )
    parser.add_argument(
        "-p", "--package",
        required=True,
        help="Имя пакета (например, nginx)"
    )
    parser.add_argument(
        "-r", "--repo", "--repos",
        required=True,
        help="remote: базовый URL репозитория; "
             "test: путь к файлу или директории с тестовыми данными"
    )
    parser.add_argument(
        "-v", "--version",
        required=True,
        help="Версия пакета (только для вывода)"
    )
    return parser.parse_args()


def parse_dependencies_from_html(html_text: str):
    """Парсим зависимости из html-страницы Alpine (секция 'Depends')."""
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
        # чистим so:
        if dep.startswith("so:"):
            dep = dep[3:]
            dep = dep.split(".")[0]

        if dep and dep not in dependencies:
            dependencies.append(dep)

    return dependencies


def parse_dependencies_from_txt(txt: str):
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

        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip())
        if deps_indent is not None and indent <= deps_indent:
            break

        dep_line = line.strip()
        if dep_line.startswith("- "):
            dep_line = dep_line[2:].strip()
        if not dep_line:
            continue

        # Режем версии: либо "name: ver", либо "name ver"
        if ":" in dep_line:
            name = dep_line.split(":", 1)[0].strip()
        else:
            name = dep_line.split()[0].strip()

        if name and name not in deps:
            deps.append(name)

    return deps


def load_content(mode: str, repo: str, package: str):
    if mode == "test":
        path = Path(repo)

        # repo — конкретный файл
        if path.is_file():
            ext = path.suffix.lower()
            text = path.read_text(encoding="utf-8")
            if ext == ".txt":
                return "txt", text
            else:
                return "html", text

        # repo — директория
        if path.is_dir():
            txt_path = path / f"{package}.txt"
            html_path = path / f"{package}.html"

            if txt_path.is_file():
                return "txt", txt_path.read_text(encoding="utf-8")
            if html_path.is_file():
                return "html", html_path.read_text(encoding="utf-8")

            print(
                f"Ошибка: не найден ни '{txt_path}', ни '{html_path}'",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"Ошибка: '{repo}' не является ни файлом, ни директорией", file=sys.stderr)
        sys.exit(1)

    # mode == remote
    base = repo.rstrip("/")
    url = f"{base}/{package}"  # без версий
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Ошибка загрузки URL '{url}': {e}", file=sys.stderr)
        sys.exit(2)

    # удалённо всегда считаем HTML
    return "html", resp.text


def build_graph_from_deps(root: str, deps: list[str]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}

    graph[root] = set(deps)
    for d in deps:
        graph.setdefault(d, set())

    return graph


def dfs_iterative(graph: dict[str, set[str]], start: str):
    WHITE, GRAY, BLACK = 0, 1, 2

    color: dict[str, int] = {}
    for node in graph.keys():
        color[node] = WHITE
    if start not in color:
        color[start] = WHITE

    visited: set[str] = set()
    cycles: list[tuple[str, str]] = []

    if color[start] != WHITE:
        # уже обходили раньше, но для простоты просто выходим
        return visited, cycles

    # стек: (node, iterator(neighbors))
    stack: list[tuple[str, object]] = []
    color[start] = GRAY
    stack.append((start, iter(graph.get(start, []))))

    while stack:
        node, it = stack[-1]
        try:
            nxt = next(it)
        except StopIteration:
            # все соседи пройдены
            stack.pop()
            color[node] = BLACK
            visited.add(node)
            continue

        c = color.get(nxt, WHITE)
        if c == WHITE:
            color[nxt] = GRAY
            stack.append((nxt, iter(graph.get(nxt, []))))
        elif c == GRAY:
            # back-edge -> цикл
            cycles.append((node, nxt))
        # BLACK — игнорируем

    return visited, cycles


def main():
    args = parse_args()

    if args.mode == "test":
        repo_path = Path(args.repo)
        if repo_path.is_dir():
            # Берём все txt-файлы и строим граф целиком
            graph = build_graph_from_txt_dir(args.repo)

            # Дальше ты можешь сделать DFS от args.package
            visited, cycles = dfs_iterative(graph, args.package)

            deps = sorted(graph.get(args.package, set()))
            transitive = {v for v in visited if v != args.package}

            print(f"package: {args.package}")
            print(f"version: {args.version}")
            print(f"mode: {args.mode}")

            print("\ndirect dependencies:")
            if deps:
                for d in deps:
                    print(f"- {d}")
            else:
                print("- (none)")

            print("\nall transitive dependencies:")
            if transitive:
                for d in sorted(transitive):
                    print(f"- {d}")
            else:
                print("- (none)")

            print("\ncycles detected:")
            if cycles:
                for u, v in cycles:
                    print(f"- {u} -> {v}")
            else:
                print("- none")

            return
        # если в test, но repo — файл, можешь оставить старую логику:
        # kind, text = load_content(...); deps = parse_... и т.д.

    # remote / остальные кейсы — твоя текущая логика
    kind, text = load_content(args.mode, args.repo, args.package)
    if kind == "txt":
        deps = parse_dependencies_from_txt(text)
    else:
        deps = parse_dependencies_from_html(text)

    graph = build_graph_from_deps(args.package, deps)
    visited, cycles = dfs_iterative(graph, args.package)

    print(f"package: {args.package}")
    print(f"version: {args.version}")
    print(f"mode: {args.mode}")

    print("\ndependencies:")
    if deps:
        for d in deps:
            print(f"- {d}")
    else:
        print("- (none)")

    print("\ngraph edges:")
    for src, targets in graph.items():
        if not targets:
            # вершина без исходящих рёбер
            print(f"{src}: (no outgoing deps)")
        else:
            for dst in targets:
                print(f"{src} -> {dst}")

    # транзитивные зависимости — всё достижимое кроме самого root
    transitive = {v for v in visited if v != args.package}

    print("\ntransitive dependencies (по текущему графу):")
    if transitive:
        for d in sorted(transitive):
            print(f"- {d}")
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

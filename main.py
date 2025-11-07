#!/usr/bin/env python3
"""
Минимальный CLI для Этапа 2 — получить прямые зависимости Maven-артифакта
(без использования менеджеров пакетов / внешних библиотек).

Пример использования:
 python maven_deps_cli.py --package org.apache.commons:commons-lang3 --version 3.12.0
 python maven_deps_cli.py -p org.apache.commons:commons-lang3 -v 3.12.0 --repo https://repo1.maven.org/maven2
 python maven_deps_cli.py -p my.group:my-artifact -v 1.0.0 --mode local --repo /path/to/local/maven/repo
"""
import argparse
import os
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_REPO = "https://repo1.maven.org/maven2"

def parse_args():
    p = argparse.ArgumentParser(description="Минимальный сборщик прямых зависимостей Maven-артифакта")
    p.add_argument("-p", "--package", required=True,
                   help="GroupId:ArtifactId (например org.apache.commons:commons-lang3)")
    p.add_argument("-r", "--repo", default=DEFAULT_REPO,
                   help=f"URL репозитория или путь к локальной директории (по умолчанию {DEFAULT_REPO})")
    p.add_argument("-m", "--mode", choices=["remote", "local"], default="remote",
                   help="Режим работы с тестовым репозиторием: remote (HTTP) или local (filesystem)")
    p.add_argument("-v", "--version", required=True, help="Версия пакета (например 3.12.0)")
    p.add_argument("-f", "--filter", default=None, help="Подстрока для фильтрации зависимостей (опционально)")
    return p.parse_args()

def print_settings(settings: dict):
    print("Параметры запуска:")
    for k, v in settings.items():
        print(f"{k}: {v}")
    print("-" * 40)

def build_pom_path(group_id: str, artifact_id: str, version: str, repo: str, mode: str):
    # groupId заменяем . -> /
    group_path = group_id.replace(".", "/")
    filename = f"{artifact_id}-{version}.pom"
    if mode == "remote":
        # Убираем возможный завершающий слеш
        repo = repo.rstrip("/")
        url = f"{repo}/{group_path}/{artifact_id}/{version}/{filename}"
        return url
    else:
        # локальный путь
        local_path = Path(repo) / group_path / artifact_id / version / filename
        return str(local_path)

def fetch_pom_remote(url: str, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                raise urllib.error.HTTPError(url, resp.status, resp.reason, resp.headers, None)
            data = resp.read()
            return data.decode('utf-8')
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP error while fetching POM: {e.code} {e.reason} ({url})")
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error while fetching POM: {e.reason} ({url})")
    except Exception as e:
        raise RuntimeError(f"Unexpected error while fetching POM: {e} ({url})")

def fetch_pom_local(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"POM not found at path: {path}")
    if not p.is_file():
        raise RuntimeError(f"Path exists but is not a file: {path}")
    return p.read_text(encoding="utf-8")

def parse_pom_and_get_dependencies(pom_xml: str):
    """
    Возвращает список зависимостей как кортежи (groupId, artifactId, version, scope)
    Только непосредственные зависимости (элемент <dependencies> внутри POM).
    """
    try:
        root = ET.fromstring(pom_xml)
    except ET.ParseError as e:
        raise RuntimeError(f"XML parse error: {e}")

    # Maven POM использует namespace в некоторых POM, поэтому поддержим оба варианта.
    # Попробуем определить namespace (если есть) и использовать его.
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0].strip("{")  # uri
        ns_prefix = f"{{{ns}}}"
    else:
        ns_prefix = ""

    deps = []
    # dependencies может быть в /project/dependencies
    deps_node = root.find(f"./{ns_prefix}dependencies")
    if deps_node is None:
        # может быть, нет зависимостей
        return deps

    for dep in deps_node.findall(f"./{ns_prefix}dependency"):
        gid = dep.find(f"./{ns_prefix}groupId")
        aid = dep.find(f"./{ns_prefix}artifactId")
        ver = dep.find(f"./{ns_prefix}version")
        scope = dep.find(f"./{ns_prefix}scope")
        gid_text = gid.text.strip() if gid is not None and gid.text else None
        aid_text = aid.text.strip() if aid is not None and aid.text else None
        ver_text = ver.text.strip() if ver is not None and ver.text else None
        scope_text = scope.text.strip() if scope is not None and scope.text else "compile"
        deps.append((gid_text, aid_text, ver_text, scope_text))
    return deps

def filter_deps(deps, substring):
    if not substring:
        return deps
    lc = substring.lower()
    return [d for d in deps if (d[0] and lc in d[0].lower()) or (d[1] and lc in d[1].lower())]

def main():
    args = parse_args()
    # Проверки параметров
    settings = {
        "package": args.package,
        "repo": args.repo,
        "mode": args.mode,
        "version": args.version,
        "filter": args.filter
    }
    print_settings(settings)

    # Разбор package -> groupId:artifactId
    if ":" not in args.package:
        print("Ошибка: параметр --package должен быть в формате groupId:artifactId", file=sys.stderr)
        sys.exit(2)
    parts = args.package.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        print("Ошибка: параметр --package некорректен. Ожидается groupId:artifactId", file=sys.stderr)
        sys.exit(2)
    group_id, artifact_id = parts

    # версия не должна быть пустой (argparse гарантия), но проверим
    version = args.version.strip()
    if not version:
        print("Ошибка: пустая версия", file=sys.stderr)
        sys.exit(2)

    try:
        path_or_url = build_pom_path(group_id, artifact_id, version, args.repo, args.mode)
        if args.mode == "remote":
            print(f"Загружаю POM по URL: {path_or_url}")
            pom_xml = fetch_pom_remote(path_or_url)
        else:
            print(f"Читаю POM локально: {path_or_url}")
            pom_xml = fetch_pom_local(path_or_url)

        deps = parse_pom_and_get_dependencies(pom_xml)
        if not deps:
            print("Прямые зависимости не найдены (пустой <dependencies> или отсутствует).")
            sys.exit(0)

        deps = filter_deps(deps, args.filter)

        print("\nПрямые зависимости (groupId : artifactId : version [scope]):")
        for g, a, v, s in deps:
            g_display = g if g else "<missing groupId>"
            a_display = a if a else "<missing artifactId>"
            v_display = v if v else "<missing version>"
            print(f"- {g_display} : {a_display} : {v_display} [{s}]")

    except FileNotFoundError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(3)
    except RuntimeError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(4)
    except Exception as e:
        print(f"Непредвиденная ошибка: {e}", file=sys.stderr)
        sys.exit(10)

if __name__ == "__main__":
    main()

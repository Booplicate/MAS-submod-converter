"""
MAS submod converter, a tool to convert old-style submods into new-style submods
(rpy to header format)
"""

__version__ = "1.0"
__author__ = "Booplicate"


import argparse
import ast
import glob
import re
import json
import io
import os
import shutil
import sys
import time
import typing


READ_CHUNK_SIZE = 2 * 1024**2

PATTERN_INIT_PY = re.compile(r"(init\s+-\d{3}\s+python(?:\s+in\s+\w+)?\s*:\n)")
PATTERN_INDENT = re.compile(r"( +)")
PATTERN_TEXT = re.compile(r"\s*(\S+)")
PATTERN_SUBMOD_START = re.compile(r"((?:(?:store.)?mas_submod_utils.)?Submod\()")


def _find_defition_bounds(header_file: typing.IO, *, quiet: bool = False):
    is_in_init_py_block = False
    is_in_submod_block = False
    submod_indent = 0
    start_line = None
    end_line = None

    if not quiet:
        print("\nParsing rpy for submod header...")

    for num, line in enumerate(header_file, start=1):
        # No text, skip
        if re.match(PATTERN_TEXT, line) is None or line.strip().startswith("#"):
            continue

        # First, find init block
        if not is_in_init_py_block:
            if re.match(PATTERN_INIT_PY, line) is not None:
                if not quiet:
                    print(f"Entering 'init python' block: {num}")
                is_in_init_py_block = True

        # We're within a block
        else:
            indent = re.match(PATTERN_INDENT, line)
            # If doesn't start with an indent, then we left the block
            if indent is None or indent.span()[0] != 0:
                if not quiet:
                    print(f"Leaving 'init python' block: {num}")
                is_in_init_py_block = False
                if is_in_submod_block:
                    if not quiet:
                        print(f"Leaving submod block: {num}")
                    is_in_submod_block = False
                    if line.strip()[0] != ")":
                        end_line = num - 1
                    else:
                        end_line = num
                    break

                if re.match(PATTERN_INIT_PY, line) is not None:
                    if not quiet:
                        print(f"Entering 'init python' block: {num}")
                    is_in_init_py_block = True

                continue

            span = indent.span()
            indent_len = span[1] - span[0]

            # Still within the block
            # Look for submod definition
            if not is_in_submod_block:
                if re.search(PATTERN_SUBMOD_START, line) is not None:
                    if not quiet:
                        print(f"Entering submod block: {num}")
                    is_in_submod_block = True
                    start_line = num
                    submod_indent = indent_len

            # Now search for the end of submod definition
            else:
                if indent_len <= submod_indent:
                    if not quiet:
                        print(f"Leaving submod block: {num}")
                    is_in_submod_block = False
                    if line.strip()[0] != ")":
                        end_line = num - 1
                    else:
                        end_line = num
                    if not quiet:
                        print(f"Leaving 'init python' block: {num}")
                    is_in_init_py_block = False
                    break

    if not quiet:
        print("Done")

    return (start_line, end_line)

def _extract_defition(
    header_file: typing.IO,
    start: int,
    end: int,
    *,
    quiet: bool = False,
    dry_run: bool = False
) -> str:
    mem_file = io.StringIO()

    for _ in range(start-1):
        line = header_file.readline()
        mem_file.write(line)

    added_pass = False
    definition_lines = []
    for _ in range(end-start+1):
        line = header_file.readline()
        stripped_line = line.strip(" \t\r\n")
        if stripped_line and not stripped_line.startswith("#"):
            if not added_pass:
                added_pass = True
                s = "pass\n"
                indent = 0
                for c in line:
                    if c == " ":
                        indent += 1
                    else:
                        break
                mem_file.write(s.rjust(len(s) + indent))

            definition_lines.append(stripped_line)

        mem_file.write("# " + line)

    while (chunk := header_file.read(READ_CHUNK_SIZE)):
        mem_file.write(chunk)

    if not quiet:
        print("\nCommenting out old definition")
    if not dry_run:
        mem_file.seek(0)
        header_file.seek(0)
        header_file.truncate(0)
        while (chunk := mem_file.read(READ_CHUNK_SIZE)):
            header_file.write(chunk)

    return "".join(definition_lines).strip(" \t\r\n")

def _parse_header_file(
    path: str,
    *,
    quiet: bool = False,
    dry_run: bool = False
) -> ast.Module|None:
    with open(path, "r+", encoding="utf-8") as header_file:
        start, end = _find_defition_bounds(header_file, quiet=quiet)
        if start is None:
            print("\nFailed to find submod definition")
            return None

        header_file.seek(0)
        data = _extract_defition(header_file, start, end, quiet=quiet, dry_run=dry_run)
        if not quiet:
            print("\nCode:", data, sep="\n")
        tree = ast.parse(data)
        if not quiet:
            print("\nAST:", ast.dump(tree, indent=4), sep="\n")
        return tree


def _get_node_value(node: ast.expr|None):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_get_node_value(el) for el in node.elts]

    if isinstance(node, ast.Dict):
        return {
            _get_node_value(item[0]): _get_node_value(item[1])
            for item in zip(node.keys, node.values)
        }

    return None

def _parse_tree(tree: ast.Module) -> dict:
    rv = {}

    pos_to_arg_name_map = {
        0: "author",
        1: "name",
        2: "version"
    }

    call = tree.body[0].value# type: ignore
    args = call.args
    kwargs = call.keywords

    for i, n in enumerate(args):
        rv[pos_to_arg_name_map[i]] = _get_node_value(n)

    for kw in kwargs:
        rv[kw.arg] = _get_node_value(kw.value)

    return rv

def _create_header(tree: ast.Module, modules: list[str]) -> dict:
    header = {
        "header_version": 1,
        "modules": list(modules)
    }
    data = _parse_tree(tree)
    header.update(**data)

    return header


def _create_out_dir(out_path: str, *, dry_run: bool = False) -> str:
    out_folder = "submod-converter-{:.0f}".format(time.time())
    out_path = os.path.join(out_path, out_folder)

    if not dry_run:
        os.makedirs(out_path, exist_ok=True)

    return out_path


def _move_assets(
    old_dir: str,
    new_dir: str,
    *,
    quiet: bool = False,
    dry_run: bool = False
):
    if not quiet:
        print(f"\nCopying files from '{old_dir}' to '{new_dir}'")

    if not dry_run:
        shutil.copytree(old_dir, new_dir, dirs_exist_ok=True)


def _convert_scripts(
    rpy_dir: str,
    *,
    quiet: bool = False,
    dry_run: bool = False
) -> list[str]:
    if not quiet:
        print("\nConverting scripts")
    modules = []

    for rpy_fp in glob.iglob("**/*.rpy*", root_dir=rpy_dir, recursive=True):
        rpy_fp = rpy_fp.replace("\\", "/")
        mod_name, ext = os.path.splitext(rpy_fp)

        if ext == ".rpyc":
            if not quiet:
                print(f"Removing compiled script '{rpy_fp}'")
            if not dry_run:
                os.remove(os.path.join(rpy_dir, rpy_fp))
            continue

        if ext != ".rpy":
            if not quiet:
                print(f"Skipping '{rpy_fp}'")
            continue

        if mod_name not in modules:
            modules.append(mod_name)

        rpy_fp_full =  os.path.join(rpy_dir, rpy_fp)
        new_fp = os.path.join(
            rpy_dir,
            mod_name + ".rpym"
        )
        if not quiet:
            print(f"Converting '{rpy_fp}' into a module")
        if not dry_run:
            os.rename(rpy_fp_full, new_fp)

    if not quiet:
        print("Done")
    return modules


def _dump_header(
    header: dict,
    out_dir: str,
    *,
    quiet: bool = False,
    dry_run: bool = False
):
    if not quiet:
        print("\nCreating submod header")

        print("Header:")
        json.dump(header, sys.stdout, indent=4)
        print("")

    if not dry_run:
        with open(os.path.join(out_dir, "header.json"), "w", encoding="utf-8") as header_file:
            json.dump(header, header_file, indent=4)

    if not quiet:
        print("Done")


def _parse_args():
    parser = argparse.ArgumentParser(
        "mas_submod_converter",
        description="Script to convert old style submods to new style"
    )
    parser.add_argument(
        "submod_dir",
        help="the submod directory (the folder with your assets, the header file and other rpy)"
    )
    parser.add_argument(
        "header_file",
        help="the file where you define your submod header, it should be within the submod directory"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="suppresses stdout output")
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="dry run (don't actually do anything)"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--out-dir",
        default=os.getcwd(),
        help="the directory to store the output data in"
    )

    return parser.parse_args()


def main():
    args = _parse_args()

    quiet = args.quiet
    dry_run = args.dry_run

    out_dir = _create_out_dir(args.out_dir, dry_run=dry_run)
    _move_assets(args.submod_dir, out_dir, quiet=quiet, dry_run=dry_run)
    tree = _parse_header_file(
        os.path.join(
            (out_dir if not dry_run else args.submod_dir),
            args.header_file
        ),
        quiet=quiet,
        dry_run=dry_run
    )
    if tree is None:
        return
    modules = _convert_scripts(
        (out_dir if not dry_run else args.submod_dir),
        quiet=quiet,
        dry_run=dry_run
    )
    header = _create_header(tree, modules)
    _dump_header(header, out_dir, quiet=quiet, dry_run=dry_run)

    if not dry_run:
        path, base = os.path.split(out_dir)
        num = base.rpartition("-")[2]
        new_out_dir = os.path.join(path, f"{header['name']}-{num}")

        os.rename(out_dir, new_out_dir)
        print(f"The output is stored in '{new_out_dir}'")


if __name__ == "__main__":
    main()

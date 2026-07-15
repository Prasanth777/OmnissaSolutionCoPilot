from __future__ import annotations

import ast
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _fragment_function() -> ast.FunctionDef:
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_hld_chat_fragment"
    )


def _calls(function: ast.FunctionDef, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
    ]


def test_chat_is_a_fragment_and_reserves_status_before_input() -> None:
    function = _fragment_function()
    assert any(
        isinstance(decorator, ast.Attribute)
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id == "st"
        and decorator.attr == "fragment"
        for decorator in function.decorator_list
    )

    empty_calls = _calls(function, "empty")
    chat_inputs = _calls(function, "chat_input")
    assert len(empty_calls) >= 2
    assert len(chat_inputs) == 1
    assert min(call.lineno for call in empty_calls) < chat_inputs[0].lineno


def test_input_is_disabled_while_work_is_pending() -> None:
    chat_input = _calls(_fragment_function(), "chat_input")[0]
    disabled = next(keyword.value for keyword in chat_input.keywords if keyword.arg == "disabled")
    assert isinstance(disabled, ast.Name)
    assert disabled.id == "is_busy"


def test_status_is_created_before_request_processing() -> None:
    function = _fragment_function()
    status_line = min(call.lineno for call in _calls(function, "status"))
    process_line = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "process_hld_chat_request"
    )
    assert status_line < process_line

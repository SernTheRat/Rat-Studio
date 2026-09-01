from __future__ import annotations
import re
import json
import os
import sys
import threading
import urllib.request
import urllib.error
import subprocess
import shutil
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk


def resource_path(filename: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / filename


@dataclass
class Token:
    kind: str
    value: object
    line: int
    column: int


class RatError(Exception):
    def __init__(self, message: str, token: Token):
        super().__init__(f"Line {token.line}, column {token.column}: {message}")
        self.line = token.line


def tokenize(source: str) -> list[Token]:
    pattern = re.compile(r'''(?P<space>[ \t\r]+)|(?P<comment>--\#[^\n]*)|(?P<number>\d+(?:\.\d+)?)|(?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(?P<name>[A-Za-z_]\w*)|(?P<op>==|!=|<=|>=|[+\-*/%<>=!{}(),;])|(?P<bad>.)''')
    tokens: list[Token] = []
    line = 1
    column = 1
    aliases = {
        "R": "let", "plague": "let", "printR": "print", "ifR": "if", "elseR": "else",
        "elifR": "elif", "whileR": "while", "trueR": "true",
        "falseR": "false", "nullR": "null", "Rnot": "not", "andT": "and", "orR": "or",
        "fang": "fn", "strike": "return", "coil": "coil", "call": "call", "bite": "bite", "shed": "shed",
    }
    keywords = {"let", "print", "if", "elif", "else", "while", "true", "false", "null", "and", "or", "not", "fn", "return", "coil", "call", "bite", "shed"}
    for match in pattern.finditer(source):
        text = match.group(0)
        kind = match.lastgroup
        token = Token(kind or "bad", text, line, column)
        if kind == "space" or kind == "comment":
            pass
        elif kind == "number":
            token.kind = "number"
            token.value = float(text) if "." in text else int(text)
            tokens.append(token)
        elif kind == "string":
            try:
                token.value = bytes(text[1:-1], "utf-8").decode("unicode_escape")
            except UnicodeDecodeError:
                raise RatError("Invalid string escape", token)
            token.kind = "string"
            tokens.append(token)
        elif kind == "name":
            normalized = aliases.get(text, text)
            token.kind = normalized if normalized in keywords else "name"
            token.value = normalized
            tokens.append(token)
        elif kind == "op":
            token.value = text
            tokens.append(token)
        else:
            raise RatError(f"Unexpected character {text!r}", token)
        line += text.count("\n")
        column = column + len(text) if "\n" not in text else len(text.rsplit("\n", 1)[1]) + 1
    tokens.append(Token("eof", "", line, column))
    return tokens


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.index = 0

    def current(self) -> Token:
        return self.tokens[self.index]

    def take(self, kind: str | None = None, value: object | None = None) -> Token:
        token = self.current()
        if (kind and token.kind != kind) or (value is not None and token.value != value):
            expected = kind or repr(value)
            raise RatError(f"Expected {expected}, got {token.value!r}", token)
        self.index += 1
        return token

    def match(self, kind: str, value: object | None = None) -> bool:
        if self.current().kind == kind and (value is None or self.current().value == value):
            self.index += 1
            return True
        return False

    def program(self):
        statements = []
        while self.current().kind != "eof":
            statements.append(self.statement())
        return ("block", statements)

    def statement(self):
        if self.match("fn"):
            name = self.take("name")
            self.take("op", "(")
            parameters = []
            if not self.match("op", ")"):
                while True:
                    self.match("bite")
                    parameters.append(self.take("name").value)
                    if self.match("op", ")"):
                        break
                    self.take("op", ",")
            body = self.braced_block(allow_coil=True)
            self.match("coil")
            return ("function", name.value, parameters, body)
        if self.match("return"):
            value = self.expression()
            self.match("op", ";")
            return ("return", value)
        if self.match("shed"):
            name = self.take("name")
            self.match("op", ";")
            return ("shed", name.value)
        if self.match("call"):
            value = self.call_expression()
            self.match("op", ";")
            return ("expression", value)
        if self.match("let"):
            name = self.take("name")
            self.take("op", "=")
            value = self.expression()
            self.match("op", ";")
            return ("let", name.value, value, name)
        if self.match("print"):
            value = self.expression()
            self.match("op", ";")
            return ("print", value)
        if self.match("if"):
            condition = self.expression()
            yes = self.braced_block()
            no = self.statement_after_if() if self.match("elif") else (self.braced_block() if self.match("else") else ("block", []))
            return ("if", condition, yes, no)
        if self.match("while"):
            condition = self.expression()
            return ("while", condition, self.braced_block())
        token = self.current()
        raise RatError("Expected let, print, if, or while", token)

    def statement_after_if(self):
        condition = self.expression()
        yes = self.braced_block()
        no = self.statement_after_if() if self.match("elif") else (self.braced_block() if self.match("else") else ("block", []))
        return ("if", condition, yes, no)

    def braced_block(self, allow_coil=False):
        self.take("op", "{")
        statements = []
        while not self.match("op", "}"):
            if allow_coil and self.match("coil"):
                return ("block", statements)
            if self.current().kind == "eof":
                raise RatError("Expected '}' before end of file", self.current())
            statements.append(self.statement())
        return ("block", statements)

    def expression(self):
        return self.binary(0)

    def binary(self, minimum: int):
        left = self.unary()
        precedence = {"or": 1, "and": 2, "==": 3, "!=": 3, "<": 4, "<=": 4, ">": 4, ">=": 4, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}
        while self.current().value in precedence and precedence[self.current().value] >= minimum:
            operation = self.take().value
            right = self.binary(precedence[operation] + 1)
            left = ("binary", operation, left, right)
        return left

    def unary(self):
        if self.match("call"):
            return self.call_expression()
        if self.current().kind in {"not"} or self.current().value in {"-", "!"}:
            operation = self.take().value
            return ("unary", operation, self.unary())
        token = self.current()
        if self.match("number") or self.match("string"):
            return ("constant", token.value)
        if self.match("true"):
            return ("constant", True)
        if self.match("false"):
            return ("constant", False)
        if self.match("null"):
            return ("constant", None)
        if self.match("name"):
            return ("variable", token.value, token)
        if self.match("op", "("):
            result = self.expression()
            self.take("op", ")")
            return result
        raise RatError("Expected a value", token)

    def call_expression(self):
        name = self.take("name")
        self.take("op", "(")
        arguments = []
        if not self.match("op", ")"):
            while True:
                arguments.append(self.expression())
                if self.match("op", ")"): break
                self.take("op", ",")
        return ("call", name.value, arguments)


class Bytecode(list):
    def __init__(self):
        super().__init__()
        self.functions: dict[str, tuple[int, list[str]]] = {}


class Compiler:
    def __init__(self):
        self.code = Bytecode()
        self.functions = self.code.functions

    def emit(self, *instruction):
        self.code.append(instruction)
        return len(self.code) - 1

    def patch(self, index: int, target: int):
        self.code[index] = (*self.code[index][:-1], target)

    def compile(self, node):
        kind = node[0]
        if kind == "block":
            for statement in node[1]: self.compile(statement)
        elif kind == "function":
            skip = self.emit("JUMP", None)
            entry = len(self.code)
            self.functions[node[1]] = (entry, node[2])
            self.compile(node[3])
            self.emit("PUSH", None)
            self.emit("RETURN")
            self.patch(skip, len(self.code))
        elif kind == "let":
            self.compile(node[2]); self.emit("STORE", node[1])
        elif kind == "print":
            self.compile(node[1]); self.emit("PRINT")
        elif kind == "return":
            self.compile(node[1]); self.emit("RETURN")
        elif kind == "shed": self.emit("SHED", node[1])
        elif kind == "expression": self.compile(node[1]); self.emit("POP")
        elif kind == "if":
            self.compile(node[1]); jump = self.emit("JUMP_IF_FALSE", None); self.compile(node[2]); skip = self.emit("JUMP", None); self.patch(jump, len(self.code)); self.compile(node[3]); self.patch(skip, len(self.code))
        elif kind == "while":
            start = len(self.code); self.compile(node[1]); jump = self.emit("JUMP_IF_FALSE", None); self.compile(node[2]); self.emit("JUMP", start); self.patch(jump, len(self.code))
        elif kind == "constant": self.emit("PUSH", node[1])
        elif kind == "variable": self.emit("LOAD", node[1])
        elif kind == "unary": self.compile(node[2]); self.emit("UNARY", node[1])
        elif kind == "binary": self.compile(node[2]); self.compile(node[3]); self.emit("BINARY", node[1])
        elif kind == "call":
            for argument in node[2]: self.compile(argument)
            self.emit("CALL", node[1], len(node[2]))


def execute(code: list[tuple], stop_requested=None) -> list[str]:
    stack: list[object] = []
    variables: dict[str, object] = {}
    functions: dict[str, tuple[int, list[str]]] = getattr(code, "functions", {})
    output: list[str] = []
    pointer = 0
    call_stack: list[tuple[int, dict[str, object]]] = []
    while pointer < len(code):
        if stop_requested is not None and stop_requested():
            raise RuntimeError("Execution stopped by user")
        instruction = code[pointer]; operation = instruction[0]
        if operation == "PUSH": stack.append(instruction[1])
        elif operation == "LOAD":
            if instruction[1] not in variables: raise RuntimeError(f"Variable {instruction[1]!r} is not defined")
            stack.append(variables[instruction[1]])
        elif operation == "STORE": variables[instruction[1]] = stack.pop()
        elif operation == "SHED": variables.pop(instruction[1], None)
        elif operation == "POP": stack.pop()
        elif operation == "PRINT": output.append(str(stack.pop()).lower() if isinstance(stack[-1], bool) else str(stack.pop()))
        elif operation == "UNARY":
            value = stack.pop(); stack.append(not value if instruction[1] in {"not", "!"} else -value)
        elif operation == "BINARY":
            right, left = stack.pop(), stack.pop(); operator = instruction[1]
            if operator == "and": result = bool(left and right)
            elif operator == "or": result = bool(left or right)
            elif operator == "==": result = left == right
            elif operator == "!=": result = left != right
            elif operator == "+": result = left + right
            elif operator == "-": result = left - right
            elif operator == "*": result = left * right
            elif operator == "/": result = left / right
            elif operator == "%": result = left % right
            else: result = {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[operator]
            stack.append(result)
        elif operation == "JUMP_IF_FALSE":
            if not stack.pop(): pointer = instruction[1]; continue
        elif operation == "JUMP": pointer = instruction[1]; continue
        elif operation == "CALL":
            function = functions.get(instruction[1])
            if function is None: raise RuntimeError(f"Function {instruction[1]!r} is not defined")
            entry, parameters = function
            arguments = [stack.pop() for _ in range(instruction[2])][::-1]
            call_stack.append((pointer + 1, variables))
            variables = dict(zip(parameters, arguments)); pointer = entry; continue
        elif operation == "RETURN":
            result = stack.pop() if stack else None
            if not call_stack: stack.append(result); break
            pointer, variables = call_stack.pop(); stack.append(result); continue
        pointer += 1
    return output


class RatTranslator:
    """Render the parsed Rat tree as source for a familiar target language."""

    def __init__(self, target: str):
        self.target = target
        self.lines: list[str] = []
        self.declared_names: set[str] = set()
        self.label_number = 0

    def translate(self, tree) -> str:
        if self.target == "JSON":
            return json.dumps(tree, indent=2, default=str)
        if self.target == "Assembly":
            return self.translate_assembly(tree)
        self.emit_block(tree, 0)
        body = "\n".join(self.lines)
        if self.target == "Binary":
            source = "\n".join(self.lines).encode("utf-8")
            return " ".join(f"{byte:02X}" for byte in source)
        if self.target == "C#":
            indented = "\n".join(f"        {line}" if line else "" for line in self.lines)
            return "using System;\n\nclass Program\n{\n    static void Main()\n    {\n" + indented + "\n    }\n}"
        if self.target == "Java":
            return "class Main {\n    public static void main(String[] args) {\n" + "\n".join(f"        {line}" for line in self.lines) + "\n    }\n}"
        if self.target == "C":
            return "#include <stdio.h>\n\nint main(void) {\n" + "\n".join(f"    {line}" for line in self.lines) + "\n    return 0;\n}"
        if self.target == "C++":
            return "#include <iostream>\n\nint main() {\n" + "\n".join(f"    {line}" for line in self.lines) + "\n    return 0;\n}"
        if self.target == "Rust":
            return "fn main() {\n" + "\n".join(f"    {line}" for line in self.lines) + "\n}"
        if self.target == "Go":
            return "package main\n\nimport \"fmt\"\n\nfunc main() {\n" + "\n".join(f"    {line}" for line in self.lines) + "\n}"
        return body

    def translate_assembly(self, tree) -> str:
        self.lines = ["; Rat AI output: x86-64 NASM-style assembly", "; External printf is used for printR statements.", "global main", "extern printf", "section .data", "    format_int db \"%ld\", 10, 0", "    format_str db \"%s\", 10, 0", "section .bss"]
        names = self.collect_variables(tree)
        self.lines.extend(f"    {name} resq 1" for name in names)
        self.lines.extend(["section .text", "main:", "    push rbp", "    mov rbp, rsp"])
        self.emit_assembly_block(tree, 0)
        self.lines.extend(["    xor eax, eax", "    pop rbp", "    ret"])
        return "\n".join(self.lines)

    def collect_variables(self, node) -> list[str]:
        found: list[str] = []
        if node[0] == "block":
            for child in node[1]: found.extend(self.collect_variables(child))
        elif node[0] == "let":
            if node[1] not in found: found.append(node[1])
        elif node[0] in {"if", "while"}:
            found.extend(self.collect_variables(node[2])); found.extend(self.collect_variables(node[3] if node[0] == "if" else ("block", [])))
        return list(dict.fromkeys(found))

    def next_label(self, prefix: str) -> str:
        self.label_number += 1
        return f".{prefix}_{self.label_number}"

    def emit_assembly_block(self, node, level):
        for statement in node[1]: self.emit_assembly_statement(statement)

    def emit_assembly_statement(self, node):
        kind = node[0]
        if kind == "let":
            self.emit_assembly_expression(node[2]); self.lines.append(f"    pop rax\n    mov [{node[1]}], rax")
        elif kind == "print":
            value = node[1]
            if value[0] == "constant" and isinstance(value[1], str):
                label = self.next_label("string"); self.lines.insert(7, f"    {label} db {value[1]!r}, 0"); self.lines.extend([f"    lea rdi, [rel format_str]", f"    lea rsi, [rel {label}]", "    xor eax, eax", "    call printf"])
            else:
                self.emit_assembly_expression(value); self.lines.extend(["    pop rsi", "    lea rdi, [rel format_int]", "    xor eax, eax", "    call printf"])
        elif kind == "if":
            end_label = self.next_label("endif"); self.emit_assembly_expression(node[1]); self.lines.extend(["    pop rax", "    test rax, rax", f"    jz {end_label}"]); self.emit_assembly_block(node[2], 0); self.lines.append(f"{end_label}:")
        elif kind == "while":
            start_label = self.next_label("while"); end_label = self.next_label("endwhile"); self.lines.append(f"{start_label}:"); self.emit_assembly_expression(node[1]); self.lines.extend(["    pop rax", "    test rax, rax", f"    jz {end_label}"]); self.emit_assembly_block(node[2], 0); self.lines.extend([f"    jmp {start_label}", f"{end_label}:"])

    def emit_assembly_expression(self, node):
        kind = node[0]
        if kind == "constant":
            value = node[1] if isinstance(node[1], (int, float, bool)) else 0
            self.lines.append(f"    push {int(value)}")
        elif kind == "variable": self.lines.append(f"    push qword [{node[1]}]")
        elif kind == "unary":
            self.emit_assembly_expression(node[2]); self.lines.append("    pop rax"); self.lines.append("    neg rax" if node[1] == "-" else "    test rax, rax\n    sete al\n    movzx rax, al"); self.lines.append("    push rax")
        elif kind == "binary":
            self.emit_assembly_expression(node[2]); self.emit_assembly_expression(node[3]); self.lines.extend(["    pop rbx", "    pop rax"])
            operations = {"+": "add rax, rbx", "-": "sub rax, rbx", "*": "imul rax, rbx"}
            self.lines.append(f"    {operations.get(node[1], 'cmp rax, rbx')}" )
            if node[1] in {"==", "!=", "<", "<=", ">", ">="}: self.lines.extend(["    sete al" if node[1] == "==" else "    setne al" if node[1] == "!=" else "    setl al", "    movzx rax, al"])
            self.lines.append("    push rax")

    def emit(self, text: str, level: int):
        self.lines.append("    " * level + text)

    def emit_block(self, node, level: int):
        for statement in node[1]:
            self.emit_statement(statement, level)

    def emit_statement(self, node, level: int):
        kind = node[0]
        if kind == "function":
            parameters = ", ".join(("$" + name if self.target == "PHP" else name) for name in node[2])
            if self.target == "Python":
                header = f"def {node[1]}({parameters}):"
            elif self.target == "Rust":
                header = f"fn {node[1]}({parameters}) {{"
            else:
                header = f"function {node[1]}({parameters}) {{"
            self.emit(header, level)
            self.emit_block(node[3], level + 1)
            if self.target != "Python":
                self.emit("}", level)
        elif kind == "return":
            suffix = ";" if self.target in {"JavaScript", "Java", "C#", "C", "C++", "PHP", "Rust", "Go"} else ""
            self.emit(f"return {self.expression(node[1])}{suffix}", level)
        elif kind == "expression":
            suffix = ";" if self.target in {"JavaScript", "Java", "C#", "C", "C++", "PHP", "Rust", "Go"} else ""
            self.emit(f"{self.expression(node[1])}{suffix}", level)
        elif kind == "shed":
            if self.target == "Python":
                self.emit(f"del {node[1]}", level)
            elif self.target == "PHP":
                self.emit(f"unset(${node[1]});", level)
        elif kind == "let":
            first_declaration = node[1] not in self.declared_names
            self.declared_names.add(node[1])
            prefix = self.declaration_prefix(first_declaration)
            suffix = ";" if self.target in {"JavaScript", "C#", "Java", "C", "C++", "PHP", "Rust", "Go", "SQL"} else ""
            name = "$" + node[1] if self.target == "PHP" else node[1]
            self.emit(f"{prefix}{name} = {self.expression(node[2])}{suffix}", level)
        elif kind == "print":
            value = self.expression(node[1])
            command = {"Python": "print", "JavaScript": "console.log", "C#": "Console.WriteLine", "Lua": "print", "Java": "System.out.println", "C": "printf", "C++": "std::cout <<", "PHP": "echo", "Rust": "println!", "Ruby": "puts", "Go": "fmt.Println", "SQL": "SELECT"}[self.target]
            suffix = ";" if self.target in {"JavaScript", "C#", "Java", "C", "C++", "PHP", "Rust", "Go", "SQL"} else ""
            if self.target == "C++": value = f"{value} << std::endl"
            if self.target == "C": value = f'"%s\\n", {value}'
            self.emit(f"{command}({value}){suffix}", level)
        elif kind == "if":
            self.emit(f"{self.control_word('if')} {self.expression(node[1])} {self.open_block('if')}", level)
            self.emit_block(node[2], level + 1)
            if node[3][1]:
                if self.target in {"JavaScript", "C#"}:
                    self.emit(self.close_block(), level)
                if node[3][0] == "if":
                    self.emit(f"{self.control_word('else if')} {self.expression(node[3][1])} {self.open_block('if')}", level)
                    self.emit_block(node[3][2], level + 1)
                    if node[3][3][1]:
                        self.emit_else(node[3][3], level)
                    elif self.target in {"JavaScript", "C#", "Lua"}:
                        self.emit(self.close_block(), level)
                else:
                    self.emit_else(node[3], level)
            elif self.target in {"JavaScript", "C#", "Lua"}:
                self.emit(self.close_block(), level)
        elif kind == "while":
            self.emit(f"{self.control_word('while')} {self.expression(node[1])} {self.open_block('while')}", level)
            self.emit_block(node[2], level + 1)
            self.emit(self.close_block(), level)

    def emit_else(self, node, level):
        self.emit("else:" if self.target == "Python" else ("else" if self.target == "Lua" else "else {"), level)
        self.emit_block(node, level + 1)
        self.emit(self.close_block(), level)

    def control_word(self, word: str) -> str:
        if self.target == "Python":
            return {"if": "if", "else if": "elif", "while": "while"}[word]
        return word

    def declaration_prefix(self, first_declaration: bool) -> str:
        if not first_declaration:
            return ""
        if self.target == "JavaScript": return "let "
        if self.target == "C#": return "dynamic "
        if self.target == "Java": return "var "
        if self.target in {"C", "C++"}: return "auto "
        if self.target == "PHP": return "$"
        if self.target == "Rust": return "let mut "
        if self.target == "Go": return "var "
        if self.target == "SQL": return "SET @"
        return ""

    def open_block(self, kind: str) -> str:
        if self.target == "Python": return ":"
        if self.target == "Lua": return "then" if kind == "if" else "do"
        return "{"

    def close_block(self) -> str:
        return "" if self.target == "Python" else ("end" if self.target == "Lua" else "}")

    def expression(self, node) -> str:
        kind = node[0]
        if kind == "constant":
            value = node[1]
            if value is None: return "None" if self.target == "Python" else ("null" if self.target == "JavaScript" else "nil" if self.target == "Lua" else "null")
            if value is True: return "True" if self.target == "Python" else "true"
            if value is False: return "False" if self.target == "Python" else "false"
            return repr(value)
        if kind == "variable": return ("$" if self.target == "PHP" else "@" if self.target == "SQL" else "") + node[1]
        if kind == "unary":
            operation = "not " if node[1] in {"not", "!"} and self.target == "Python" else ("!" if node[1] in {"not", "!"} else "-")
            return f"({operation}{self.expression(node[2])})"
        if kind == "binary":
            operator = node[1]
            if operator == "and": operator = "and" if self.target in {"Python", "Lua", "Ruby"} else "&&"
            if operator == "or": operator = "or" if self.target in {"Python", "Lua", "Ruby"} else "||"
            if self.target == "Rust" and operator == "==": operator = "=="
            return f"({self.expression(node[2])} {operator} {self.expression(node[3])})"
        if kind == "call":
            arguments = ", ".join(self.expression(argument) for argument in node[2])
            return f"{node[1]}({arguments})"
        raise RatError("Rat AI cannot translate this expression", Token("translation", "", 1, 1))


def translate_source(source: str, target: str) -> str:
    if target == "Binary":
        return " ".join(f"{byte:02X}" for byte in source.encode("utf-8"))
    tree = Parser(tokenize(source)).program()
    return RatTranslator(target).translate(tree)


DEMO = '''--# Welcome to Rat\nR snacks = 3;\nwhileR snacks > 0 {\n    printR("Snack time!");\n    R snacks = snacks - 1;\n}\n\nifR snacks == 0 {\n    printR("All done.");\n} elseR {\n    printR("Still hungry.");\n}'''
APP_VERSION = "0.0"


class RatStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Rat Studio v{APP_VERSION}")
        self.geometry("1100x720")
        # The three main panes require roughly 160 + 500 + 230 px.
        # Keep a realistic minimum so Tk never compresses panes into each other.
        self.minsize(940, 620)
        self.configure(bg="#008080")
        self.protocol("WM_DELETE_WINDOW", self.close_app)

        self.settings_path = Path(__file__).parent / "rat_studio_settings.json"
        self.theme = self.load_settings()
        self.keyword_colors = {k: v for k, v in self.theme.setdefault("keyword_colors", {}).items() if k in self.DEFAULT_THEME["keyword_colors"]}
        self.state_path = Path(__file__).parent / "rat_studio_state.json"
        self.app_state = self.load_app_state()
        self.theme_widgets = {}
        self.titlebar = None
        self.titlebar_title = None
        self.rat_emblem = None
        self.current_file: Path | None = None
        self.project_root = Path(__file__).parent / "projects"
        self.project_root.mkdir(exist_ok=True)
        self.translation_target = tk.StringVar(value="Python")
        self.ai_messages: list[dict[str, str]] = []
        self.tabs: dict[str, dict[str, object]] = {"main.rat": {"path": None, "content": DEMO}}
        self.active_tab = "main.rat"
        self.last_error_line: int | None = None
        self.stop_requested = False
        self.fold_ranges: dict[int, int] = {}
        self.collapsed_lines: set[int] = set()
        self.notepad_window: tk.Toplevel | None = None
        self.bind_all("<Control-KeyPress-q>", self.open_notepad)
        self.bind_all("<Control-Shift-KeyPress-U>", self.new_tab)
        self.bind_all("<Control-KeyPress-s>", self.save_shortcut)
        self.bind_all("<F5>", self.run_shortcut)
        self.mascot_image = None
        mascot_path = resource_path("rat_mascot.png")
        if mascot_path.exists():
            self.mascot_image = tk.PhotoImage(file=str(mascot_path))
            self.iconphoto(True, self.mascot_image)
        self.editor_font_size = 10
        self._build_ui()
        self.editor.insert("1.0", DEMO)
        self.editor.edit_modified(False)
        self._highlight()
        self.apply_theme()
        # Rat AI is a required dependency for this build. On first launch,
        # block the editor until Ollama + llama3.2 are ready.
        self.after(400, self.ensure_ollama_ready)

    def _dedupe_keyword_color_widgets(self):
        """Keep the keyword customization data unique and refresh safely."""
        colors = self.app_state.get("keyword_colors", {})
        if isinstance(colors, dict):
            self.app_state["keyword_colors"] = dict(colors)
        return self.app_state["keyword_colors"]

    def _build_ui(self):
        # Classic Windows 95/98-inspired Rat UI:
        # chunky 3D controls, gray chrome, teal title bars, and rat-brown accents.
        style = ttk.Style(self)
        style.theme_use("clam")

        win_gray = "#c0c0c0"
        win_light = "#ffffff"
        win_dark = "#808080"
        win_deep = "#404040"
        win_blue = "#000080"
        rat_brown = "#8b5a3c"
        rat_dark = "#5a3826"
        rat_light = "#c89b7b"
        desktop = "#008080"

        self.configure(bg=desktop)

        # Classic raised/sunken button look.
        style.configure(
            "TButton",
            background=win_gray,
            foreground="#000000",
            padding=(8, 4),
            borderwidth=2,
            relief="raised",
            font=("MS Sans Serif", 9, "bold"),
        )
        style.map(
            "TButton",
            background=[("active", "#d8d8d8"), ("pressed", "#a0a0a0")],
            relief=[("pressed", "sunken")],
        )
        style.configure(
            "Accent.TButton",
            background=rat_light,
            foreground="#000000",
            padding=(8, 4),
            borderwidth=2,
            relief="raised",
            font=("MS Sans Serif", 9, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#ddb49b"), ("pressed", "#a8785b")],
            relief=[("pressed", "sunken")],
        )
        style.configure(
            "TLabel",
            background=win_gray,
            foreground="#000000",
            font=("MS Sans Serif", 9),
        )
        style.configure(
            "Status.TLabel",
            background=win_gray,
            foreground="#000000",
            font=("MS Sans Serif", 8),
            relief="sunken",
            borderwidth=1,
            padding=(5, 2),
        )

        # ---------- Main application window ----------
        main = tk.Frame(self, bg=win_gray, bd=2, relief="raised")
        main.pack(fill="both", expand=True, padx=6, pady=6)

        # Windows-style title bar.
        titlebar = tk.Frame(main, bg=self.theme.get("titlebar", win_blue), height=28)
        titlebar.pack(fill="x")
        titlebar.pack_propagate(False)
        self.titlebar = titlebar
        self.theme_widgets["titlebar"] = titlebar

        emblem = tk.Canvas(
            titlebar, width=23, height=23,
            bg=self.theme.get("titlebar", win_blue), highlightthickness=0
        )
        self.rat_emblem = emblem
        emblem.pack(side="left", padx=3, pady=2)

        # Tiny pixel-ish rat icon.
        eye_color = self.theme.get("eye_color", "#c81e1e")
        emblem.create_oval(5, 9, 18, 19, fill=rat_light, outline="#000000")
        emblem.create_oval(6, 3, 11, 10, fill=rat_light, outline="#000000")
        emblem.create_oval(13, 3, 18, 10, fill=rat_light, outline="#000000")
        emblem.create_oval(9, 11, 11, 13, fill=eye_color, outline="")
        emblem.create_oval(14, 11, 16, 13, fill=eye_color, outline="")
        emblem.create_line(18, 16, 22, 19, fill=rat_light, width=1)

        self.titlebar_title = tk.Label(
            titlebar,
            text=f"Rat Studio v{APP_VERSION}",
            bg=self.theme.get("titlebar", win_blue),
            fg="#ffffff",
            font=("MS Sans Serif", 10, "bold"),
            anchor="w",
        )
        self.titlebar_title.pack(side="left", padx=2)

        # Dedicated Settings gear in the title bar.
        # Clicking it opens the full Rat Studio customization window.
        self.settings_icon = tk.Canvas(
            titlebar, width=27, height=22, bg=win_blue,
            highlightthickness=0, bd=1, relief="raised", cursor="hand2"
        )
        self.settings_icon.pack(side="right", padx=(2, 1), pady=3)

        def draw_titlebar_settings(pressed=False, hover=False):
            self.settings_icon.delete("all")
            bg = "#d8d8d8" if hover else win_gray
            self.settings_icon.configure(bg=bg, relief="sunken" if pressed else "raised")
            cx, cy = 13, 11
            gear = self.theme.get("accent", rat_brown)
            for dx, dy in ((0,-7),(5,-5),(7,0),(5,5),(0,7),(-5,5),(-7,0),(-5,-5)):
                self.settings_icon.create_rectangle(
                    cx+dx-2, cy+dy-2, cx+dx+2, cy+dy+2,
                    fill=gear, outline="#000000"
                )
            self.settings_icon.create_oval(6,4,20,18, fill=gear, outline="#000000")
            self.settings_icon.create_oval(11,9,15,13, fill=bg, outline="#000000")

        def open_settings_from_icon(_event=None):
            draw_titlebar_settings(pressed=True, hover=True)
            self.after(80, self.show_settings)

        self.settings_icon.bind("<Button-1>", open_settings_from_icon)
        self.settings_icon.bind("<Enter>", lambda _e: draw_titlebar_settings(hover=True))
        self.settings_icon.bind("<Leave>", lambda _e: draw_titlebar_settings())
        self.settings_icon.bind("<ButtonRelease-1>", lambda _e: draw_titlebar_settings(hover=True))
        draw_titlebar_settings()

        # Real working classic window controls.
        controls = (
            ("_", self.minimize_window),
            ("□", self.toggle_maximize),
            ("X", self.close_app),
        )
        for symbol, command in controls:
            tk.Button(
                titlebar, text=symbol, command=command, width=2, height=1,
                bg=win_gray, fg="#000000", activebackground="#d8d8d8",
                activeforeground="#000000", relief="raised", bd=2,
                font=("Arial", 8, "bold"), cursor="arrow"
            ).pack(side="right", padx=(1, 2), pady=3)

        # Real Windows-style IDE menu bar.
        menubar = tk.Frame(main, bg=win_gray, height=25)
        menubar.pack(fill="x")
        menubar.pack_propagate(False)

        def add_menu(label, commands):
            button = tk.Menubutton(
                menubar, text=label, bg=win_gray, fg="#000000",
                activebackground=win_blue, activeforeground="#ffffff",
                font=("MS Sans Serif", 9), padx=8, pady=2, relief="flat", bd=0
            )
            menu = tk.Menu(button, tearoff=False, bg="#c0c0c0", fg="#000000",
                           activebackground=win_blue, activeforeground="#ffffff",
                           font=("MS Sans Serif", 9))
            for item in commands:
                if item is None:
                    menu.add_separator()
                elif isinstance(item, tuple) and item[0] == "check":
                    menu.add_checkbutton(label=item[1], variable=item[2], command=item[3])
                else:
                    label_text, command = item
                    menu.add_command(label=label_text, command=command)
            button.configure(menu=menu)
            button.pack(side="left")
            return button

        self.word_wrap_var = tk.BooleanVar(value=False)
        self.status_bar_var = tk.BooleanVar(value=True)

        add_menu("File", [
            ("New Rat File", self.new_file),
            ("New Tab", self.new_tab),
            ("Open Rat File...", self.open_file),
            ("Open Project...", self.open_project),
            None,
            ("Save", self.save_file),
            ("Save All", self.save_all),
            None,
            ("Close Tab", lambda: self.close_tab(self.active_tab)),
            ("Exit", self.close_app),
        ])
        add_menu("Edit", [
            ("Undo", lambda: self.editor.event_generate("<<Undo>>")),
            ("Redo", lambda: self.editor.event_generate("<<Redo>>")),
            None,
            ("Cut", lambda: self.editor.event_generate("<<Cut>>")),
            ("Copy", lambda: self.editor.event_generate("<<Copy>>")),
            ("Paste", lambda: self.editor.event_generate("<<Paste>>")),
            None,
            ("Find / Replace...", self.find_replace),
            ("Go to Line...", self.go_to_line),
            ("Format Rat Code", self.format_code),
        ])
        add_menu("Run", [
            ("Run Rat   F5", self.run),
            ("Compile", self.compile),
            ("Lint", self.lint_code),
            ("Stop", self.stop),
            None,
            ("Translate", self.translate),
        ])
        add_menu("View", [
            ("Expand All", self.expand_all),
            ("Find / Replace...", self.find_replace),
            ("Zoom In", lambda: self.change_editor_font(1)),
            ("Zoom Out", lambda: self.change_editor_font(-1)),
            ("Reset Zoom", lambda: self.change_editor_font(0, reset=True)),
            ("Toggle Word Wrap", lambda: self.toggle_word_wrap()),
            ("Toggle Translation View", self.toggle_translation_view),
            ("Packages", self.show_packages),
        ])
        add_menu("Rat AI", [
            ("Ask Rat AI", self.ask_ai),
            ("Explain Current Code", lambda: self.assistant_prompt("Explain this Rat code clearly.")),
            ("Find Bugs", self.lint_code),
            ("Generate Rat Code", lambda: self.assistant_prompt("Generate Rat code for my request.")),
            None,
            ("AI Setup", self.show_ai_setup),
        ])
        add_menu("Settings", [
            ("Rat Studio Settings", self.show_settings),
        ])
        add_menu("Help", [
            ("Rat Basics", self.show_basics),
            ("Keyboard Shortcuts", self.show_shortcuts),
            ("About Rat Studio", self.show_about),
        ])

        # Toolbar with chunky Windows controls.
        toolbar = tk.Frame(main, bg=win_gray, bd=1, relief="raised")
        toolbar.pack(fill="x", padx=2, pady=(0, 3))

        for text_label, command, accent in (
            ("NEW", self.new_file, False),
            ("OPEN", self.open_file, False),
            ("SAVE", self.save_file, False),
            ("COMPILE", self.compile, False),
            ("TRANSLATE", self.translate, False),
            ("STOP", self.stop, False),
            ("RUN RAT", self.run, True),
        ):
            ttk.Button(
                toolbar,
                text=text_label,
                command=command,
                style="Accent.TButton" if accent else "TButton",
                width=10,
            ).pack(side="left", padx=2, pady=3)

        tk.Label(
            toolbar,
            text="  TRANSLATE:",
            bg=win_gray,
            fg="#000000",
            font=("MS Sans Serif", 8, "bold"),
        ).pack(side="left", padx=(10, 2))

        translate_box = ttk.Combobox(
            toolbar,
            textvariable=self.translation_target,
            values=(
                "Python", "JavaScript", "Java", "C#", "C", "C++", "PHP",
                "Rust", "Lua", "Ruby", "Go", "SQL", "JSON", "Binary", "Assembly"
            ),
            state="readonly",
            width=12,
            font=("MS Sans Serif", 9),
        )
        translate_box.pack(side="left", padx=2, pady=3)
        translate_box.bind("<<ComboboxSelected>>", lambda _event: self.target_status.configure(text=f"Target: {self.translation_target.get()}"))

        # Main workspace.
        workspace = tk.PanedWindow(
            main,
            orient="horizontal",
            bg=win_dark,
            sashwidth=5,
            bd=2,
            relief="sunken",
        )
        workspace.pack(fill="both", expand=True, padx=3, pady=3)
        self._main_workspace = workspace

        # ---------- File explorer ----------
        files = tk.Frame(workspace, bg=win_gray, bd=2, relief="sunken", width=205)
        workspace.add(files, minsize=160, stretch="never")

        tk.Label(
            files,
            text="RAT FILES",
            bg=rat_brown,
            fg="#ffffff",
            font=("MS Sans Serif", 9, "bold"),
            anchor="w",
            padx=6,
            pady=3,
        ).pack(fill="x", padx=2, pady=2)

        self.project_label = tk.Label(
            files,
            text=self.project_root.name.upper(),
            bg=win_gray,
            fg="#000000",
            font=("MS Sans Serif", 8, "bold"),
            anchor="w",
        )
        self.project_label.pack(fill="x", padx=7, pady=(6, 2))

        file_box = tk.Frame(files, bg=win_light, bd=2, relief="sunken")
        file_box.pack(fill="both", expand=True, padx=5, pady=3)

        self.project_files = tk.Listbox(
            file_box,
            height=1,
        )
        self.project_files.destroy()
        self.project_files = ttk.Treeview(file_box, show="tree", selectmode="browse")
        self.project_files.pack(fill="both", expand=True, padx=2, pady=2)
        self.project_files.bind("<<TreeviewSelect>>", self.open_selected_file)

        ttk.Button(files, text="New File", command=self.new_file).pack(
            fill="x", padx=5, pady=(5, 2)
        )
        ttk.Button(files, text="Open Folder", command=self.open_project).pack(
            fill="x", padx=5, pady=2
        )
        file_actions = tk.Frame(files, bg=win_gray)
        file_actions.pack(fill="x", padx=5, pady=2)
        ttk.Button(file_actions, text="Rename", command=self.rename_file).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(file_actions, text="Delete", command=self.delete_file).pack(side="left", fill="x", expand=True, padx=(2, 0))

        tk.Label(
            files,
            text="RAT TOOLS",
            bg=rat_brown,
            fg="#ffffff",
            font=("MS Sans Serif", 8, "bold"),
            anchor="w",
            padx=6,
            pady=3,
        ).pack(fill="x", padx=2, pady=(8, 2))

        for label, command in (
            ("Compile", self.compile),
            ("Run", self.run),
            ("Formatter", self.format_code),
            ("Linter", self.lint_code),
            ("Packages", self.show_packages),
        ):
            ttk.Button(files, text=label, command=command).pack(
                fill="x", padx=5, pady=2
            )

        # ---------- Editor / output ----------
        center = tk.Frame(workspace, bg=win_gray)
        workspace.add(center, minsize=500, stretch="always")

        body = tk.PanedWindow(
            center,
            orient="vertical",
            bg=win_dark,
            sashwidth=5,
            bd=2,
            relief="sunken",
        )
        body.pack(fill="both", expand=True)
        self._editor_body = body

        editor_frame = tk.Frame(body, bg=win_gray, bd=2, relief="sunken")
        body.add(editor_frame, minsize=280, stretch="always")

        editor_header = tk.Frame(editor_frame, bg=rat_brown, height=25)
        editor_header.pack(fill="x")
        editor_header.pack_propagate(False)

        self.editor_header_label = tk.Label(
            editor_header,
            text=" RAT SOURCE   |   main.rat",
            bg=rat_brown,
            fg="#ffffff",
            font=("MS Sans Serif", 8, "bold"),
            anchor="w",
        )
        self.editor_header_label.pack(fill="both", expand=True)
        tk.Button(editor_header, text="EXPAND ALL", command=self.expand_all, bg="#c0c0c0", fg="#000000", relief="raised", bd=2, font=("MS Sans Serif", 7, "bold")).pack(side="right", padx=3, pady=2)

        # Tabs.
        self.tab_bar = tk.Frame(editor_frame, bg=win_gray, height=28)
        self.tab_bar.pack(fill="x")
        self.tab_bar.pack_propagate(False)

        editor_pane = tk.Frame(editor_frame, bg=win_gray)
        editor_pane.pack(fill="both", expand=True, padx=2, pady=2)

        self.line_numbers = tk.Text(
            editor_pane,
            width=6,
            bg="#e0e0e0",
            fg="#606060",
            font=("Courier New", 10),
            state="disabled",
            bd=1,
            relief="sunken",
            padx=5,
            pady=8,
        )
        self.line_numbers.pack(side="left", fill="y")
        self.line_numbers.bind("<Button-1>", self.toggle_fold)
        self.line_numbers.configure(cursor="hand2")

        self.editor = tk.Text(
            editor_pane,
            bg="#ffffff",
            fg="#000000",
            insertbackground="#000000",
            selectbackground="#000080",
            selectforeground="#ffffff",
            font=("Courier New", 10),
            undo=True,
            bd=1,
            relief="sunken",
            padx=8,
            pady=8,
            wrap="none",
            tabs=("2c",),
        )
        self.editor.tag_configure("keyword", foreground=rat_brown, font=("Courier New", 10, "bold"))
        self.editor.tag_configure("string", foreground="#008000")
        self.editor.tag_configure("comment", foreground="#808080")
        self.editor.tag_configure("error_line", background="#ffd6d6")
        self.editor.tag_configure("folded", elide=True)
        self.editor.pack(side="left", fill="both", expand=True)

        self.editor.bind("<KeyPress>", self.handle_editor_key)
        self.editor.bind("<BackSpace>", self.handle_editor_backspace)
        self.editor.bind("<KeyRelease>", self.editor_changed)
        self.editor.bind("<Control-f>", self.find_replace)
        self.editor.bind("<Control-h>", self.find_replace)
        self.editor.bind("<Control-g>", self.go_to_line)
        self.editor.bind("<Control-w>", lambda _e: self.close_tab(self.active_tab) or "break")
        self.editor.bind("<Control-Shift-s>", lambda _e: self.save_as() or "break")
        self.editor.configure(yscrollcommand=lambda first, last: self.line_numbers.yview_moveto(first))
        self.line_numbers.configure(yscrollcommand=lambda first, last: self.editor.yview_moveto(first))

        # Output console.
        output_frame = tk.Frame(body, bg=win_gray, bd=2, relief="sunken")
        body.add(output_frame, minsize=120, stretch="always")

        output_header = tk.Frame(output_frame, bg="#000000", height=25)
        output_header.pack(fill="x")
        output_header.pack_propagate(False)
        tk.Button(output_header, text="VIEW TRANSLATION", command=self.toggle_translation_view, bg="#c0c0c0", fg="#000000", relief="raised", bd=2, font=("MS Sans Serif", 7, "bold")).pack(side="right", padx=2, pady=2)
        tk.Label(
            output_header,
            text=" OUTPUT / RAT VM CONSOLE",
            bg="#000000",
            fg="#00ff00",
            font=("Courier New", 8, "bold"),
            anchor="w",
            pady=3,
        ).pack(fill="x")

        self.output = tk.Text(
            output_frame,
            bg="#000000",
            fg="#00ff00",
            insertbackground="#00ff00",
            font=("Courier New", 10),
            state="disabled",
            bd=1,
            relief="sunken",
            padx=8,
            pady=7,
            wrap="word",
        )
        self.output.pack(fill="both", expand=True, padx=2, pady=2)
        self.translation_view = tk.Text(
            output_frame, bg="#ffffff", fg="#000000", font=("Courier New", 10),
            state="disabled", bd=1, relief="sunken", padx=8, pady=7, wrap="none"
        )

        # ---------- Rat AI ----------
        assistant = tk.Frame(
            workspace,
            bg=win_gray,
            bd=2,
            relief="sunken",
            width=270,
        )
        workspace.add(assistant, minsize=230, stretch="never")

        tk.Label(
            assistant,
            text="RAT AI ASSISTANT",
            bg=win_blue,
            fg="#ffffff",
            font=("MS Sans Serif", 9, "bold"),
            anchor="w",
            padx=7,
            pady=4,
        ).pack(fill="x", padx=2, pady=2)

        chat_box = tk.Frame(assistant, bg=win_light, bd=2, relief="sunken")
        chat_box.pack(fill="both", expand=True, padx=7, pady=3)

        self.ai_chat = tk.Text(
            chat_box,
            height=20,
            bg="#ffffff",
            fg="#000000",
            font=("MS Sans Serif", 9),
            wrap="word",
            state="disabled",
            bd=0,
            padx=7,
            pady=7,
        )
        self.ai_chat.pack(fill="both", expand=True)

        self.add_ai_message(
            "assistant",
            "Welcome to Rat AI!\n\n"
            "I'm your tiny programming rat. "
            "Ask me about Rat, your code, bugs, or translations."
        )

        ai_buttons = tk.Frame(assistant, bg=win_gray)
        ai_buttons.pack(fill="x", padx=5, pady=4)

        for action, command in (
            ("Explain Code", lambda: self.assistant_prompt("Explain this Rat code clearly.")),
            ("Find Bugs", self.lint_code),
            ("Optimize", lambda: self.assistant_prompt("Suggest useful improvements to this Rat code.")),
            ("Generate Code", lambda: self.assistant_prompt("Generate Rat code for the request I will describe.")),
            ("Translate Code", self.translate),
            ("Setup AI", self.show_ai_setup),
        ):
            ttk.Button(ai_buttons, text=action, command=command).pack(
                fill="x", padx=2, pady=2
            )

        ask_row = tk.Frame(assistant, bg=win_gray)
        ask_row.pack(fill="x", padx=7, pady=(3, 7))

        self.ai_input = tk.Entry(
            ask_row,
            bg="#ffffff",
            fg="#000000",
            insertbackground="#000000",
            relief="sunken",
            bd=2,
            font=("MS Sans Serif", 9),
        )
        self.ai_input.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 4))
        self.ai_input.bind("<Return>", lambda _: self.ask_ai())

        ttk.Button(
            ask_row,
            text="ASK",
            width=5,
            command=self.ask_ai,
            style="Accent.TButton",
        ).pack(side="right")

        # Mouse wheel support.
        for widget in (
            self.editor,
            self.line_numbers,
            self.output,
            self.ai_chat,
            self.project_files,
        ):
            widget.bind("<MouseWheel>", self.handle_mousewheel, add="+")

        # Classic status bar.
        statusbar = tk.Frame(main, bg=win_gray)
        statusbar.pack(fill="x", padx=2, pady=(1, 2))

        self.status = ttk.Label(
            statusbar,
            text="● Ready | Rat source",
            style="Status.TLabel",
            anchor="w",
        )
        self.status.pack(side="left", fill="x", expand=True)
        self.cursor_status = tk.Label(statusbar, text="Ln 1, Col 1", bg=win_gray, fg="#000000", bd=1, relief="sunken", padx=7, pady=2, font=("MS Sans Serif", 8))
        self.cursor_status.pack(side="right", padx=(4, 0))
        self.encoding_status = tk.Label(statusbar, text="UTF-8", bg=win_gray, fg="#000000", bd=1, relief="sunken", padx=7, pady=2, font=("MS Sans Serif", 8))
        self.encoding_status.pack(side="right", padx=(4, 0))
        self.target_status = tk.Label(statusbar, text="Target: Python", bg=win_gray, fg="#000000", bd=1, relief="sunken", padx=7, pady=2, font=("MS Sans Serif", 8))
        self.target_status.pack(side="right", padx=(4, 0))

        tk.Label(
            statusbar,
            text=f"RAT STUDIO {APP_VERSION}",
            bg=win_gray,
            fg="#404040",
            font=("MS Sans Serif", 8),
            bd=1,
            relief="sunken",
            padx=7,
            pady=2,
        ).pack(side="right", padx=(4, 0))

        self.refresh_project_files()
        self.refresh_tabs()
        self.after_idle(self._set_initial_panes)

    # ---------------- Theme / settings ----------------
    DEFAULT_THEME = {
        "titlebar": "#6b3a1f",
        "accent": "#8b5a3c",
        "rat_light": "#c89b7b",
        "rat_dark": "#5a3826",
        "desktop": "#008080",
        "console_bg": "#000000",
        "console_fg": "#00ff00",
        "eye_color": "#c81e1e",
        "keyword_colors": {
            "R": "#8b5a3c",
            "plague": "#8b3d8b",
            "printR": "#0066cc",
            "ifR": "#b00020",
            "elifR": "#b00020",
            "elseR": "#b00020",
            "whileR": "#9a5b00",
            "infect": "#9a5b00",
            "cheese": "#c28b00",
            "to": "#0055aa",
            "trueR": "#008000",
            "falseR": "#b00020",
            "nullR": "#666666",
            "Rnot": "#0055aa",
            "andT": "#0055aa",
            "orR": "#0055aa",
            "fang": "#8b3d8b",
            "bite": "#007777",
            "strike": "#7a3e00",
            "call": "#0055aa",
            "coil": "#7a3e00",
            "shed": "#7a3e00",
        },
    }

    def load_settings(self):
        data = json.loads(json.dumps(self.DEFAULT_THEME))
        try:
            if self.settings_path.exists():
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    for key, value in saved.items():
                        if key == "keyword_colors" and isinstance(value, dict):
                            allowed = set(data["keyword_colors"])
                            data["keyword_colors"].update({k: v for k, v in value.items() if k in allowed and isinstance(v, str)})
                        elif key in data and isinstance(value, str):
                            data[key] = value
        except Exception:
            pass
        return data

    def save_settings(self):
        try:
            self.theme["keyword_colors"] = self.keyword_colors
            self.settings_path.write_text(json.dumps(self.theme, indent=2), encoding="utf-8")
        except Exception:
            pass

    def load_app_state(self):
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def save_app_state(self):
        try:
            self.state_path.write_text(json.dumps(self.app_state, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _set_initial_panes(self):
        """Set sensible starting split positions once; never fight manual resizing."""
        try:
            workspace = getattr(self, "_main_workspace", None)
            body = getattr(self, "_editor_body", None)
            if workspace and workspace.winfo_exists():
                width = workspace.winfo_width()
                if width > 0:
                    left = 180
                    right = 240
                    if width - right > left + 500:
                        workspace.sash_place(0, left, 0)
                        workspace.sash_place(1, width - right, 0)
            if body and body.winfo_exists():
                height = body.winfo_height()
                if height > 0:
                    body.sash_place(0, 0, max(280, int(height * 0.68)))
        except tk.TclError:
            pass

    def close_app(self):
        self.save_settings()
        self.destroy()

    def minimize_window(self):
        self.iconify()

    def toggle_maximize(self):
        try:
            if self.state() == "zoomed":
                self.state("normal")
            else:
                self.state("zoomed")
        except tk.TclError:
            pass

    @staticmethod
    def hsv_to_hex(h, s, v):
        import colorsys
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return "#%02x%02x%02x" % (round(r*255), round(g*255), round(b*255))

    @staticmethod
    def hex_to_hsv(value):
        import colorsys
        value = value.lstrip("#")
        if len(value) != 6:
            raise ValueError("Use a 6-digit HEX color")
        r, g, b = (int(value[i:i+2], 16) / 255 for i in (0, 2, 4))
        return colorsys.rgb_to_hsv(r, g, b)

    def color_picker(self, parent, initial, callback):
        top = tk.Toplevel(parent)
        top.title("Choose Rat Color")
        top.geometry("360x445")
        top.resizable(False, False)
        top.configure(bg="#000000")
        top.transient(parent)
        top.grab_set()

        wheel_size = 210
        wheel = tk.Canvas(top, width=wheel_size, height=wheel_size, bg="#000000", highlightthickness=0)
        wheel.pack(pady=(12, 5))
        top.update_idletasks()
        img = tk.PhotoImage(width=wheel_size, height=wheel_size)
        import colorsys, math
        cx = cy = wheel_size / 2
        radius = wheel_size / 2 - 8
        rows = []
        for y in range(wheel_size):
            row = []
            for x in range(wheel_size):
                dx, dy = x-cx, y-cy
                dist = math.hypot(dx, dy)
                if dist <= radius:
                    sat = min(1.0, dist/radius)
                    hue = (math.atan2(dy, dx) / (2*math.pi)) % 1.0
                    rr, gg, bb = colorsys.hsv_to_rgb(hue, sat, 1.0)
                    row.append("#%02x%02x%02x" % (int(rr*255), int(gg*255), int(bb*255)))
                else:
                    row.append("#000000")
            rows.append(row)
        for y, row in enumerate(rows):
            img.put("{" + " ".join(row) + "}", to=(0, y))
        wheel.image = img
        wheel.create_image(0, 0, image=img, anchor="nw")

        try:
            h, sat, val = self.hex_to_hsv(initial)
        except ValueError:
            h, sat, val = 0.08, 0.55, 0.55
        state = {"h": h, "s": sat, "v": val}
        picker_closed = {"value": False}
        marker = wheel.create_oval(0, 0, 0, 0, outline="#ffffff", width=3)
        marker2 = wheel.create_oval(0, 0, 0, 0, outline="#000000", width=1)

        preview = tk.Label(top, text="#000000", bg="#000000", fg="#ffffff", font=("Consolas", 11, "bold"), width=12, pady=5)
        preview.pack(pady=(4, 2))
        hex_row = tk.Frame(top, bg="#000000")
        hex_row.pack(pady=2)
        tk.Label(hex_row, text="HEX", bg="#000000", fg="#ffffff", font=("MS Sans Serif", 9, "bold")).pack(side="left", padx=4)
        hex_var = tk.StringVar(value=initial if initial.startswith("#") else "#000000")
        hex_entry = tk.Entry(hex_row, textvariable=hex_var, width=10, bg="#222222", fg="#ffffff", insertbackground="#ffffff",
                             relief="sunken", bd=2, font=("Consolas", 10))
        hex_entry.pack(side="left")
        brightness = tk.Scale(top, from_=100, to=0, orient="horizontal", length=300, showvalue=False,
                              bg="#000000", fg="#ffffff", troughcolor="#ffffff", highlightthickness=0)
        brightness.set(val*100)
        brightness.pack(pady=3)

        def refresh():
            color = self.hsv_to_hex(state["h"], state["s"], state["v"])
            preview.configure(text=color, bg=color, fg="#ffffff" if state["v"] < .65 else "#000000")
            hex_var.set(color)
            px = cx + math.cos(state["h"] * 2 * math.pi) * state["s"] * radius
            py = cy + math.sin(state["h"] * 2 * math.pi) * state["s"] * radius
            wheel.coords(marker, px-7, py-7, px+7, py+7)
            wheel.coords(marker2, px-5, py-5, px+5, py+5)
            callback(color)

        def hex_changed(_event=None):
            try:
                hh, ss, vv = self.hex_to_hsv(hex_var.get())
            except ValueError:
                return
            state["h"], state["s"], state["v"] = hh, ss, vv
            brightness.set(vv * 100)
            refresh()

        def wheel_click(event):
            dx, dy = event.x-cx, event.y-cy
            dist = math.hypot(dx, dy)
            if dist > radius:
                return
            state["h"] = (math.atan2(dy, dx) / (2*math.pi)) % 1.0
            state["s"] = min(1.0, dist/radius)
            refresh()

        def brightness_changed(value):
            state["v"] = float(value)/100.0
            refresh()

        wheel.bind("<Button-1>", wheel_click)
        wheel.bind("<B1-Motion>", wheel_click)
        brightness.configure(command=brightness_changed)
        hex_entry.bind("<Return>", hex_changed)
        hex_entry.bind("<FocusOut>", hex_changed)
        def finish_picker():
            picker_closed["value"] = True
            top.destroy()

        def cancel_picker():
            if not picker_closed["value"]:
                callback(initial)
            picker_closed["value"] = True
            top.destroy()

        buttons = tk.Frame(top, bg="#000000")
        buttons.pack(pady=8)
        tk.Button(buttons, text="CANCEL", command=cancel_picker, bg="#c0c0c0", relief="raised", bd=2, width=10).pack(side="left", padx=4)
        tk.Button(buttons, text="DONE", command=finish_picker, bg="#c89b7b", relief="raised", bd=2, width=10).pack(side="left", padx=4)
        top.protocol("WM_DELETE_WINDOW", cancel_picker)
        refresh()

    def apply_theme(self):
        desktop = self.theme.get("desktop", "#008080")
        self.configure(bg=desktop)
        if self.titlebar:
            self.titlebar.configure(bg=self.theme["titlebar"])
        if self.titlebar_title:
            self.titlebar_title.configure(bg=self.theme["titlebar"])
        if self.rat_emblem:
            self.rat_emblem.configure(bg=self.theme["titlebar"])
            self.rat_emblem.delete("all")
            light = self.theme["rat_light"]
            eyes = self.theme.get("eye_color", "#c81e1e")
            self.rat_emblem.create_oval(5, 9, 18, 19, fill=light, outline="#000000")
            self.rat_emblem.create_oval(6, 3, 11, 10, fill=light, outline="#000000")
            self.rat_emblem.create_oval(13, 3, 18, 10, fill=light, outline="#000000")
            self.rat_emblem.create_oval(9, 11, 11, 13, fill=eyes, outline="")
            self.rat_emblem.create_oval(14, 11, 16, 13, fill=eyes, outline="")
            self.rat_emblem.create_line(18, 16, 22, 19, fill=light, width=1)
        if hasattr(self, "settings_icon"):
            # Keep the settings icon synchronized with the chosen Rat accent.
            self.settings_icon.delete("all")
            cx, cy = 15, 13
            gear_color = self.theme["accent"]
            for dx, dy in (
                (0, -9), (6, -6), (9, 0), (6, 6),
                (0, 9), (-6, 6), (-9, 0), (-6, -6)
            ):
                self.settings_icon.create_rectangle(
                    cx + dx - 2, cy + dy - 2,
                    cx + dx + 2, cy + dy + 2,
                    fill=gear_color, outline="#000000"
                )
            self.settings_icon.create_oval(
                7, 5, 23, 21, fill=gear_color, outline="#000000", width=1
            )
            self.settings_icon.create_oval(
                12, 10, 18, 16, fill="#c0c0c0", outline="#000000"
            )
        style = ttk.Style(self)
        style.configure("Accent.TButton", background=self.theme["rat_light"], foreground="#000000")
        style.map("Accent.TButton", background=[("active", self.theme["rat_light"]), ("pressed", self.theme["rat_dark"])])
        if hasattr(self, "output"):
            self.output.configure(bg=self.theme["console_bg"], fg=self.theme["console_fg"], insertbackground=self.theme["console_fg"])
        # Update tagged keyword colors and re-style the editor.
        for keyword, color in self.keyword_colors.items():
            self.editor.tag_configure(f"kw_{keyword}", foreground=color, font=("Courier New", 10, "bold"))
        self._highlight()

    def show_settings(self, original_theme=None, original_keyword_colors=None):
        win = tk.Toplevel(self)
        win.title("Rat Studio Settings")
        win.geometry("650x660")
        win.minsize(650, 500)
        win.resizable(True, True)
        win.configure(bg="#c0c0c0")
        win.transient(self)
        original_theme = json.loads(json.dumps(self.theme)) if original_theme is None else original_theme
        original_keyword_colors = json.loads(json.dumps(self.keyword_colors)) if original_keyword_colors is None else original_keyword_colors
        win.protocol("WM_DELETE_WINDOW", lambda: self.cancel_settings(win, original_theme, original_keyword_colors))

        tk.Label(win, text="RAT STUDIO SETTINGS", bg=self.theme["titlebar"], fg="#ffffff",
                 font=("MS Sans Serif", 10, "bold"), anchor="w", padx=8, pady=5).pack(fill="x", padx=3, pady=3)
        tk.Label(win, text="Choose a color, then customize individual compiler keywords below.",
                 bg="#c0c0c0", fg="#000000", font=("MS Sans Serif", 9)).pack(anchor="w", padx=10, pady=6)
        tk.Label(win, text="Click any color swatch to open the color wheel. Save keeps the window open; Save & Close saves and exits.",
             bg="#c0c0c0", fg="#404040", font=("MS Sans Serif", 8, "italic")).pack(anchor="w", padx=10, pady=(0, 5))

        # Reserve the button bar's space at the bottom of the window FIRST (side="bottom"),
        # before packing the color lists above, so Save/Cancel/Reset always stay visible and
        # reachable no matter how many rows the theme or keyword lists grow to.
        bottom = tk.Frame(win, bg="#c0c0c0")
        bottom.pack(side="bottom", fill="x", padx=10, pady=8)
        tk.Button(bottom, text="RESET TO DEFAULT", command=lambda: self.reset_theme(win, original_theme, original_keyword_colors), bg="#c0c0c0", relief="raised", bd=2).pack(side="left")
        tk.Button(bottom, text="CANCEL", command=lambda: self.cancel_settings(win, original_theme, original_keyword_colors), bg="#c0c0c0", relief="raised", bd=2).pack(side="right", padx=(5, 0))
        tk.Button(bottom, text="SAVE & CLOSE", command=lambda: (self.save_settings(), win.destroy()), bg="#c89b7b", relief="raised", bd=2).pack(side="right")
        save_status = tk.Label(bottom, text="", bg="#c0c0c0", fg="#2e7d32", font=("MS Sans Serif", 8, "italic"))
        save_status.pack(side="right", padx=(0, 8))

        def save_only():
            self.save_settings()
            save_status.configure(text="Saved!")
            win.after(1500, lambda: save_status.configure(text="") if save_status.winfo_exists() else None)

        tk.Button(bottom, text="SAVE", command=save_only, bg="#c89b7b", relief="raised", bd=2).pack(side="right", padx=(0, 5))

        colors_frame = tk.LabelFrame(win, text="Theme Colors", bg="#c0c0c0", fg="#000000", bd=2, relief="groove",
                                     font=("MS Sans Serif", 9, "bold"))
        colors_frame.pack(fill="x", padx=10, pady=5)
        color_names = [
            ("Title Bar", "titlebar"), ("Rat Accent", "accent"), ("Rat Light", "rat_light"),
            ("Rat Dark", "rat_dark"), ("Rat Eyes", "eye_color"), ("Desktop", "desktop"),
            ("Console Background", "console_bg"), ("Console Text", "console_fg"),
        ]
        for row, (label, key) in enumerate(color_names):
            tk.Label(colors_frame, text=label, bg="#c0c0c0", anchor="w", width=22).grid(row=row, column=0, padx=6, pady=3, sticky="w")
            swatch = tk.Button(colors_frame, text=self.theme[key], bg=self.theme[key], width=14, relief="raised", bd=2)
            swatch.grid(row=row, column=1, padx=5, pady=3)
            def choose(k=key, b=swatch):
                def changed(color):
                    self.theme[k] = color
                    b.configure(text=color, bg=color, fg="#ffffff" if self.hex_to_hsv(color)[2] < .65 else "#000000")
                    self.apply_theme()
                self.color_picker(win, self.theme[k], changed)
            swatch.configure(command=choose)

        kw_frame = tk.LabelFrame(win, text="Compiler Keyword Colors", bg="#c0c0c0", fg="#000000", bd=2, relief="groove",
                                 font=("MS Sans Serif", 9, "bold"))
        kw_frame.pack(fill="both", expand=True, padx=10, pady=5)
        kw_canvas = tk.Canvas(kw_frame, bg="#c0c0c0", highlightthickness=0, height=190)
        kw_scroll = ttk.Scrollbar(kw_frame, orient="vertical", command=kw_canvas.yview)
        kw_inner = tk.Frame(kw_canvas, bg="#c0c0c0")
        kw_inner.bind("<Configure>", lambda e: kw_canvas.configure(scrollregion=kw_canvas.bbox("all")))
        kw_canvas.create_window((0, 0), window=kw_inner, anchor="nw")
        kw_canvas.configure(yscrollcommand=kw_scroll.set)
        kw_canvas.pack(side="left", fill="both", expand=True, padx=3, pady=3)
        kw_scroll.pack(side="right", fill="y", pady=3)
        for row, keyword in enumerate(self.keyword_colors):
            tk.Label(kw_inner, text=keyword, bg="#c0c0c0", fg=self.keyword_colors[keyword],
                     font=("Courier New", 10, "bold"), width=14, anchor="w").grid(row=row, column=0, padx=6, pady=2, sticky="w")
            btn = tk.Button(kw_inner, text=self.keyword_colors[keyword], bg=self.keyword_colors[keyword], width=14, relief="raised", bd=2)
            btn.grid(row=row, column=1, padx=6, pady=2)
            def choose_kw(k=keyword, b=btn):
                def changed(color):
                    self.keyword_colors[k] = color
                    b.configure(text=color, bg=color, fg="#ffffff" if self.hex_to_hsv(color)[2] < .65 else "#000000")
                    self.apply_theme()
                self.color_picker(win, self.keyword_colors[k], changed)
            btn.configure(command=choose_kw)

    def cancel_settings(self, settings_window, original_theme, original_keyword_colors):
        self.theme = original_theme
        self.keyword_colors = original_keyword_colors
        self.apply_theme()
        settings_window.destroy()
        self.status.configure(text="●  Settings canceled  |  colors restored")

    def reset_theme(self, settings_window=None, original_theme=None, original_keyword_colors=None):
        self.theme = json.loads(json.dumps(self.DEFAULT_THEME))
        self.keyword_colors = self.theme["keyword_colors"]
        self.apply_theme()
        self.save_settings()
        if settings_window is not None:
            settings_window.destroy()
            self.show_settings(self.theme, self.keyword_colors)

    def handle_editor_key(self, event):
        pairs = {"(": ")", "[": "]", "{": "}", "'": "'", '"': '"'}
        if event.char in pairs:
            closing = pairs[event.char]
            try:
                selected = self.editor.get("sel.first", "sel.last")
                self.editor.delete("sel.first", "sel.last")
                self.editor.insert("insert", event.char + selected + closing)
                self.editor.mark_set("insert", f"insert-{len(closing)}c")
            except tk.TclError:
                next_character = self.editor.get("insert", "insert+1c")
                if next_character == closing:
                    self.editor.mark_set("insert", "insert+1c")
                else:
                    self.editor.insert("insert", event.char + closing)
                    self.editor.mark_set("insert", f"insert-{len(closing)}c")
            self._highlight()
            return "break"
        if event.char in (")", "]", "}") and self.editor.get("insert", "insert+1c") == event.char:
            self.editor.mark_set("insert", "insert+1c")
            return "break"
        return None

    def handle_editor_backspace(self, _event):
        matching = {"(": ")", "[": "]", "{": "}", "'": "'", '"': '"'}
        previous = self.editor.get("insert-1c", "insert")
        following = self.editor.get("insert", "insert+1c")
        if matching.get(previous) == following:
            self.editor.delete("insert-1c", "insert+1c")
            self._highlight()
            return "break"
        return None

    def editor_changed(self, _event=None):
        self.store_active_tab()
        was_dirty = self.tabs[self.active_tab].get("dirty", False)
        self.tabs[self.active_tab]["dirty"] = True
        self.editor.edit_modified(False)
        self._highlight()
        self.update_cursor_status()
        if not was_dirty:
            self.refresh_tabs()

    def update_cursor_status(self, _event=None):
        if not hasattr(self, "editor"):
            return
        line, column = self.editor.index("insert").split(".")
        self.cursor_status.configure(text=f"Ln {line}, Col {int(column) + 1}")

    def save_shortcut(self, _event=None):
        self.save_file()
        return "break"

    def run_shortcut(self, _event=None):
        self.run()
        return "break"

    def assistant_help(self):
        self.ai_input.focus_set()

    def assistant_prompt(self, prompt: str):
        self.ai_input.delete(0, "end")
        self.ai_input.insert(0, prompt)
        self.ai_input.focus_set()

    def refresh_project_files(self):
        for item in self.project_files.get_children():
            self.project_files.delete(item)
        root_item = self.project_files.insert("", "end", text=self.project_root.name, open=True, values=(str(self.project_root),))
        for path in sorted(self.project_root.glob("*.rat")):
            self.project_files.insert(root_item, "end", text=f"R  {path.name}", values=(str(path),))
        self.project_label.configure(text=self.project_root.name.upper())

    def refresh_tabs(self):
        if hasattr(self, "editor_header_label"):
            self.editor_header_label.configure(text=f" RAT SOURCE   |   {self.active_tab}")
        for child in self.tab_bar.winfo_children():
            child.destroy()
        for name in self.tabs:
            background = "#39243c" if name == self.active_tab else "#1b211b"
            foreground = "#f1e4ee" if name == self.active_tab else "#9aa69b"
            tab = tk.Frame(self.tab_bar, bg=background, height=28)
            tab.pack(side="left", fill="y", padx=(3, 0), pady=3)
            tab.bind("<Button-1>", lambda _event, tab_name=name: self.switch_tab(tab_name))
            dirty = "*" if self.tabs[name].get("dirty", False) else ""
            label = tk.Label(tab, text=f"R  {name}{dirty}", bg=background, fg=foreground, relief="flat", padx=8, font=("Segoe UI", 9, "bold"))
            label.pack(side="left", fill="y")
            label.bind("<Button-1>", lambda _event, tab_name=name: self.switch_tab(tab_name))
            close = tk.Button(tab, text="×", command=lambda tab_name=name: self.close_tab(tab_name), bg=background, fg=foreground, activebackground="#c87555", activeforeground="#ffffff", relief="flat", bd=0, padx=5, font=("Segoe UI", 10, "bold"))
            close.pack(side="right", fill="y")
        basics = tk.Button(self.tab_bar, text="?  Rat Basics", command=self.show_basics, bg="#c0c0c0", fg="#000000", relief="raised", bd=2, padx=8, font=("MS Sans Serif", 8, "bold"))
        basics.pack(side="left", fill="y", padx=(8, 2), pady=2)
        tk.Button(self.tab_bar, text="+", command=self.new_tab, bg="#111611", fg="#c87555", relief="flat", bd=0, font=("Segoe UI", 13, "bold")).pack(side="left", padx=6)

    def show_basics(self):
        window = tk.Toplevel(self)
        window.title("Rat Basics")
        window.geometry("760x560")
        window.minsize(620, 440)
        window.configure(bg="#c0c0c0")
        window.transient(self)

        title = tk.Frame(window, bg=self.theme["titlebar"], height=30)
        title.pack(fill="x", padx=3, pady=3)
        tk.Label(title, text="RAT BASICS  /  BEGINNER'S GUIDE", bg=self.theme["titlebar"], fg="#ffffff", font=("MS Sans Serif", 10, "bold"), anchor="w", padx=8).pack(fill="both", expand=True)

        intro = tk.Label(window, text="Learn the small building blocks of Rat, then load an example into a new editor tab.", bg="#c0c0c0", fg="#000000", font=("MS Sans Serif", 9), anchor="w")
        intro.pack(fill="x", padx=10, pady=(5, 7))

        content = tk.Frame(window, bg="#c0c0c0")
        content.pack(fill="both", expand=True, padx=10, pady=4)
        lessons = tk.Listbox(content, width=22, bg="#ffffff", fg="#000000", selectbackground="#000080", selectforeground="#ffffff", font=("MS Sans Serif", 9), relief="sunken", bd=2)
        lessons.pack(side="left", fill="y", padx=(0, 8))
        lesson_text = tk.Text(content, bg="#ffffff", fg="#000000", font=("Courier New", 10), wrap="word", relief="sunken", bd=2, padx=10, pady=8)
        lesson_text.pack(side="left", fill="both", expand=True)

        topics = {
            "Variables": ("R creates a variable. Names can be reused to update a value.\n\nExample:\nR age = 15;\nprintR(age);", "R age = 15;\nprintR(age);"),
            "Printing": ("printR writes a value to the Rat console. Use either single or double quotes for text.\n\nExample:\nprintR('Hello, Rat!');", "printR('Hello, Rat!');"),
            "Conditions": ("ifR chooses code when a condition is true. Add elseR for the other path.\n\nExample:\nifR age >= 13 {\n    printR(\"Welcome!\");\n} elseR {\n    printR(\"Too young!\");\n}", "R age = 15;\nifR age >= 13 {\n    printR(\"Welcome!\");\n} elseR {\n    printR(\"Too young!\");\n}"),
            "Loops": ("whileR repeats a block while its condition is true. Change a variable inside the loop so it can finish.\n\nExample:\nR count = 3;\nwhileR count > 0 {\n    printR(count);\n    R count = count - 1;\n}", "R count = 3;\nwhileR count > 0 {\n    printR(count);\n    R count = count - 1;\n}"),
            "Functions": ("fang defines a function, bite names a parameter, strike returns a value, and call runs it.\n\nExample:\nfang greet(bite name) {\n    strike name;\n}\nprintR(call greet(\"Rat\"));", "fang greet(bite name) {\n    strike name;\n}\nprintR(call greet(\"Rat\"));"),
            "Values": ("Rat supports numbers, text, trueR, falseR, and nullR. Use ==, !=, <, >, <=, and >= to compare values.\n\nExample:\nR alive = trueR;\nR missing = nullR;\nprintR(alive);", "R alive = trueR;\nR missing = nullR;\nprintR(alive);"),
        }
        example = {"value": ""}
        for topic in topics:
            lessons.insert("end", topic)

        def display(_event=None):
            selection = lessons.curselection()
            if not selection:
                return
            name = lessons.get(selection[0])
            explanation, example_source = topics[name]
            example["value"] = example_source
            lesson_text.configure(state="normal")
            lesson_text.delete("1.0", "end")
            lesson_text.insert("1.0", explanation)
            lesson_text.configure(state="disabled")

        lessons.bind("<<ListboxSelect>>", display)
        lessons.selection_set(0)
        display()

        footer = tk.Frame(window, bg="#c0c0c0")
        footer.pack(fill="x", padx=10, pady=8)
        tk.Button(footer, text="LOAD EXAMPLE IN NEW TAB", command=lambda: self.load_basics_example(example["value"]), bg="#c89b7b", fg="#000000", relief="raised", bd=2).pack(side="left")
        tk.Button(footer, text="CLOSE", command=window.destroy, bg="#c0c0c0", fg="#000000", relief="raised", bd=2).pack(side="right")

    def load_basics_example(self, source: str):
        self.new_tab()
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", source + "\n")
        self.editor_changed()
        self._highlight()
        self._draw_fold_gutter()
        self.status.configure(text="●  Basics example loaded  |  press F5 to run")

    def close_tab(self, name: str):
        if len(self.tabs) == 1:
            self.status.configure(text="●  Keep one tab open  |  create a new tab first")
            return
        self.store_active_tab()
        if name == self.active_tab:
            names = list(self.tabs)
            next_name = names[names.index(name) - 1] if names.index(name) > 0 else names[1]
            del self.tabs[name]
            self.active_tab = next_name
            self.current_file = self.tabs[next_name]["path"]
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", self.tabs[next_name]["content"])
            self._highlight()
        else:
            del self.tabs[name]
        self.refresh_tabs()
        self.status.configure(text=f"●  Closed tab  |  {name}")

    def handle_mousewheel(self, event):
        widget = event.widget
        if event.delta:
            widget.yview_scroll(-int(event.delta / 120), "units")
            return "break"
        return None

    def store_active_tab(self):
        self.tabs[self.active_tab]["content"] = self.editor.get("1.0", "end-1c")

    def switch_tab(self, name: str):
        if name == self.active_tab:
            return
        self.store_active_tab()
        self.active_tab = name
        tab = self.tabs[name]
        self.current_file = tab["path"]
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", tab["content"])
        self._highlight()
        self._draw_fold_gutter()
        self.refresh_tabs()
        self.editor_header_label.configure(text=f" RAT SOURCE   |   {name}")
        self.status.configure(text=f"●  Tab  |  {name}")

    def new_tab(self, _event=None):
        self.store_active_tab()
        number = 1
        while f"untitled-{number}.rat" in self.tabs:
            number += 1
        name = f"untitled-{number}.rat"
        self.tabs[name] = {"path": None, "content": "--# New Rat file\n", "dirty": True}
        self.active_tab = name
        self.current_file = None
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.tabs[name]["content"])
        try:
            self.editor.edit_modified(False)
        except tk.TclError:
            pass
        self._highlight()
        self.refresh_tabs()
        if hasattr(self, "editor_header_label"):
            self.editor_header_label.configure(text=f" RAT SOURCE   |   {name}")
        self.status.configure(text=f"●  New tab  |  {name}  |  unsaved")

    def open_notepad(self, _event=None):
        if self.notepad_window is not None and self.notepad_window.winfo_exists():
            self.notepad_window.deiconify(); self.notepad_window.lift(); self.notepad_text.focus_set(); return "break"
        self.notepad_window = tk.Toplevel(self)
        self.notepad_window.title("Rat Notepad")
        self.notepad_window.geometry("620x460")
        self.notepad_window.configure(bg="#141914")
        tk.Label(self.notepad_window, text="RAT NOTEPAD  /  Ctrl+Q", bg="#202720", fg="#f1f5f1", font=("Segoe UI", 12, "bold"), anchor="w", padx=14, pady=10).pack(fill="x")
        self.notepad_text = tk.Text(self.notepad_window, bg="#0b0d0b", fg="#d6dfd6", insertbackground="#f2c6b6", font=("Consolas", 11), undo=True, wrap="word", bd=0, padx=14, pady=14)
        self.notepad_text.pack(fill="both", expand=True, padx=10, pady=10)
        footer = tk.Frame(self.notepad_window, bg="#141914"); footer.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(footer, text="Save Notes", command=self.save_notes).pack(side="right")
        ttk.Button(footer, text="Clear", command=lambda: self.notepad_text.delete("1.0", "end")).pack(side="right", padx=6)
        self.notepad_window.protocol("WM_DELETE_WINDOW", self.notepad_window.withdraw)
        self.notepad_text.focus_set()
        return "break"

    def save_notes(self):
        filename = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt"), ("All files", "*.*")], parent=self.notepad_window)
        if filename:
            Path(filename).write_text(self.notepad_text.get("1.0", "end-1c"), encoding="utf-8")

    def open_selected_file(self, _event=None):
        selection = self.project_files.selection()
        if not selection:
            return
        values = self.project_files.item(selection[0], "values")
        if not values:
            return
        path = Path(values[0])
        if path.suffix.lower() != ".rat":
            return
        self.open_path_in_tab(path)
        self.status.configure(text=f"●  Opened  |  {path.name}")

    def selected_project_path(self):
        selection = self.project_files.selection()
        if not selection:
            return None
        values = self.project_files.item(selection[0], "values")
        return Path(values[0]) if values else None

    def rename_file(self):
        path = self.selected_project_path()
        if path is None or path.suffix.lower() != ".rat":
            return
        name = simpledialog.askstring("Rename Rat file", "New file name:", initialvalue=path.name, parent=self)
        if name:
            if not name.endswith(".rat"):
                name += ".rat"
            new_path = path.with_name(Path(name).name)
            path.rename(new_path)
            self.refresh_project_files()
            self.status.configure(text=f"●  Renamed  |  {new_path.name}")

    def delete_file(self):
        path = self.selected_project_path()
        if path is None or path.suffix.lower() != ".rat":
            return
        if messagebox.askyesno("Delete Rat file", f"Delete {path.name}?", parent=self):
            path.unlink(missing_ok=True)
            for name, tab in list(self.tabs.items()):
                if tab.get("path") == path:
                    del self.tabs[name]
            if not self.tabs:
                self.new_tab()
            self.refresh_project_files()
            self.refresh_tabs()
            self.status.configure(text=f"●  Deleted  |  {path.name}")

    def open_path_in_tab(self, path: Path):
        self.store_active_tab()
        name = path.name
        self.tabs[name] = {"path": path, "content": path.read_text(encoding="utf-8")}
        self.active_tab = name
        self.current_file = path
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.tabs[name]["content"])
        self._highlight()
        self.refresh_tabs()

    def open_project(self):
        folder = filedialog.askdirectory(initialdir=str(self.project_root), title="Open Rat project folder")
        if folder:
            self.project_root = Path(folder)
            self.refresh_project_files()
            self.status.configure(text=f"●  Project  |  {self.project_root}")

    def format_code(self):
        source = self.editor.get("1.0", "end-1c")
        formatted = re.sub(r"\s*\{", " {", source)
        formatted = re.sub(r"\s*\}", "\n}", formatted)
        formatted = re.sub(r";\s*", ";\n", formatted)
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", formatted.strip() + "\n")
        self._highlight()
        self.status.configure(text="●  Formatted  |  Rat source")

    def lint_code(self):
        try:
            tree = Parser(tokenize(self.editor.get("1.0", "end-1c"))).program()
            defined = set()
            for statement in tree[1]:
                if statement[0] == "let": defined.add(statement[1])
            self._show("Lint passed.\nNo top-level syntax errors found.", "#67d39a")
            self.status.configure(text=f"●  Lint passed  |  {len(defined)} variable(s) found")
        except (RatError, RuntimeError) as error:
            self._show(f"Lint error:\n{error}", "#ee8d76")
            self.status.configure(text="●  Lint failed  |  fix the reported issue")

    def show_packages(self):
        self._show("Rat package manager\n\nNo external packages are required for the current Rat runtime.\nPackage imports are reserved for a future release.", "#d6e0d6")
        self.status.configure(text="●  Packages  |  standard library only")

    def show_ai_setup(self):
        self.add_ai_message("assistant", "Rat AI setup is required. Rat Studio will check Ollama, install it if needed, download llama3.2, and start the local AI service.")
        self.status.configure(text="●  AI setup  |  checking Ollama...")
        self.show_ollama_setup_popup(force=False)

    def _ollama_executable(self):
        candidates = [
            shutil.which("ollama"),
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"),
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "ollama.exe"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _ollama_api_ready(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=1.5) as r:
                return r.status == 200
        except Exception:
            return False

    def _model_installed(self, ollama):
        try:
            result = subprocess.run(
                [ollama, "list"], capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.returncode == 0 and any(line.strip().startswith("llama3.2") for line in result.stdout.splitlines())
        except Exception:
            return False

    def ensure_ollama_ready(self):
        if sys.platform != "win32":
            self.show_ollama_setup_popup(force=True, unsupported=True)
            return
        if self._ollama_executable() is None:
            self.show_ollama_setup_popup(force=True)
        else:
            self.show_ollama_setup_popup(force=True, skip_install=True)

    def show_ollama_setup_popup(self, force=True, skip_install=False, unsupported=False):
        win = tk.Toplevel(self)
        win.title("Rat AI — Required Setup")
        win.geometry("620x500")
        win.resizable(False, False)
        win.configure(bg="#c0c0c0")
        win.transient(self)
        win.grab_set()
        if force:
            win.protocol("WM_DELETE_WINDOW", lambda: None)

        tk.Label(win, text="RAT AI SETUP", bg=self.theme.get("titlebar", "#000080"), fg="#ffffff",
                 font=("MS Sans Serif", 11, "bold"), anchor="w", padx=8, pady=6).pack(fill="x", padx=3, pady=3)

        tk.Label(win, text="Rat AI requires Ollama + llama3.2 to run locally.", bg="#c0c0c0", fg="#000000",
                 font=("MS Sans Serif", 10, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 3))
        tk.Label(win, text="This setup runs the official Ollama Windows installer, then downloads the model and starts the local service.",
                 bg="#c0c0c0", fg="#404040", font=("MS Sans Serif", 9), anchor="w", justify="left").pack(fill="x", padx=14, pady=(0, 10))

        steps = tk.Frame(win, bg="#c0c0c0", bd=2, relief="sunken")
        steps.pack(fill="x", padx=14, pady=4)
        step_labels = {}
        for i, name in enumerate(("Install Ollama", "Download llama3.2", "Start local AI", "Verify Rat AI"), 1):
            row = tk.Frame(steps, bg="#ffffff")
            row.pack(fill="x", padx=2, pady=1)
            step_labels[i] = tk.Label(row, text=f"{i}. {name}  — waiting", bg="#ffffff", fg="#000000",
                                      font=("MS Sans Serif", 9), anchor="w", padx=8, pady=5)
            step_labels[i].pack(fill="x")

        progress = ttk.Progressbar(win, orient="horizontal", mode="determinate", maximum=100)
        progress.pack(fill="x", padx=14, pady=(12, 5))
        progress_label = tk.Label(win, text="Preparing...", bg="#c0c0c0", fg="#404040",
                                  font=("MS Sans Serif", 8), anchor="w")
        progress_label.pack(fill="x", padx=14)

        log_box = tk.Text(win, height=8, bg="#000000", fg="#00ff00", font=("Courier New", 8),
                          state="disabled", bd=2, relief="sunken")
        log_box.pack(fill="both", expand=True, padx=14, pady=8)

        buttons = tk.Frame(win, bg="#c0c0c0")
        buttons.pack(fill="x", padx=14, pady=(0, 12))
        install_btn = tk.Button(buttons, text="INSTALL / SET UP RAT AI", bg="#c89b7b", relief="raised", bd=2, width=24)
        install_btn.pack(side="left")
        close_btn = tk.Button(buttons, text="CLOSE", bg="#c0c0c0", relief="raised", bd=2, width=10,
                              state="disabled" if force else "normal", command=win.destroy)
        close_btn.pack(side="right")

        def log(msg):
            def ui():
                if not win.winfo_exists(): return
                log_box.configure(state="normal")
                log_box.insert("end", msg.rstrip() + "\n")
                log_box.see("end")
                log_box.configure(state="disabled")
            self.after(0, ui)

        def set_step(n, text):
            def ui():
                if not win.winfo_exists(): return
                for i in range(1, 5):
                    if i < n:
                        step_labels[i].configure(text=step_labels[i].cget("text").split("  —")[0] + "  — done", fg="#2e7d32")
                    elif i == n:
                        step_labels[i].configure(text=step_labels[i].cget("text").split("  —")[0] + f"  — {text}", fg="#8b5a3c")
            self.after(0, ui)

        def set_progress(value, text):
            self.after(0, lambda: (progress.configure(value=max(0, min(100, value))), progress_label.configure(text=text)))

        def finish(ok, message):
            def ui():
                if not win.winfo_exists(): return
                if ok:
                    for i in range(1, 5):
                        step_labels[i].configure(text=step_labels[i].cget("text").split("  —")[0] + "  — done", fg="#2e7d32")
                    set_progress(100, "Rat AI is ready.")
                    install_btn.configure(text="RAT AI READY", state="disabled")
                    close_btn.configure(state="normal")
                    self.app_state["ai_setup_shown"] = True
                    self.save_app_state()
                    self.status.configure(text="●  Rat AI connected  |  llama3.2")
                else:
                    install_btn.configure(text="RETRY SETUP", state="normal")
                    close_btn.configure(state="normal" if not force else "disabled")
                    progress_label.configure(text=message)
            self.after(0, ui)

        def worker():
            if unsupported:
                log("Rat AI auto-setup currently supports Windows builds.")
                finish(False, "Windows is required for this installer build.")
                return

            try:
                ollama = self._ollama_executable()
                if ollama is None:
                    set_step(1, "downloading installer")
                    set_progress(5, "Downloading official Ollama installer...")
                    installer = Path(os.environ.get("TEMP", str(Path.home()))) / "OllamaSetup.exe"
                    url = "https://ollama.com/download/OllamaSetup.exe"
                    log("Downloading: " + url)
                    urllib.request.urlretrieve(url, installer)
                    log("Installer downloaded.")
                    set_progress(20, "Launching Ollama installer...")
                    log("Starting OllamaSetup.exe. Complete the installer if Windows asks for confirmation.")
                    proc = subprocess.Popen([str(installer)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    proc.wait()
                    time.sleep(2)
                    ollama = self._ollama_executable()
                    if ollama is None:
                        raise RuntimeError("Ollama installer finished, but the ollama command was not found. Please restart Rat Studio and try again.")
                else:
                    set_step(1, "already installed")
                    set_progress(25, "Ollama is already installed.")
                    log("Ollama detected: " + ollama)

                set_step(2, "checking model")
                if not self._model_installed(ollama):
                    set_progress(35, "Downloading llama3.2...")
                    log("Running: ollama pull llama3.2")
                    proc = subprocess.Popen(
                        [ollama, "pull", "llama3.2"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    for line in proc.stdout or []:
                        line = line.strip()
                        if line:
                            log(line)
                            pct = 35
                            m = re.search(r"(\d+)%", line)
                            if m:
                                pct = 35 + int(m.group(1)) * 0.45
                            set_progress(pct, "Downloading llama3.2..." if pct < 80 else "Finishing llama3.2...")
                    code = proc.wait()
                    if code != 0:
                        raise RuntimeError("ollama pull llama3.2 failed. Check your Internet connection and available disk space.")
                else:
                    set_step(2, "already installed")
                    set_progress(80, "llama3.2 is already installed.")
                    log("llama3.2 already installed.")

                set_step(3, "starting service")
                set_progress(85, "Starting local Ollama service...")
                if not self._ollama_api_ready():
                    log("Starting: ollama serve")
                    subprocess.Popen([ollama, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for _ in range(30):
                    if self._ollama_api_ready(): break
                    time.sleep(0.5)
                if not self._ollama_api_ready():
                    raise RuntimeError("Ollama installed, but its local API did not start on port 11434.")

                set_step(4, "verifying")
                set_progress(95, "Verifying Rat AI connection...")
                log("Ollama API is online at http://localhost:11434")
                finish(True, "Rat AI is ready.")
            except Exception as e:
                log("ERROR: " + str(e))
                finish(False, str(e))

        def start():
            install_btn.configure(state="disabled", text="SETTING UP...")
            close_btn.configure(state="disabled")
            threading.Thread(target=worker, daemon=True).start()

        install_btn.configure(command=start)
        if skip_install:
            start()
        else:
            # For a forced first-run setup, begin automatically.
            if force:
                start()

    def add_ai_message(self, role: str, text: str):

        self.ai_chat.configure(state="normal")
        label = "YOU" if role == "user" else "RAT AI"
        self.ai_chat.insert("end", f"{label}\n{text}\n\n")
        self.ai_chat.see("end")
        self.ai_chat.configure(state="disabled")

    def ask_ai(self):
        question = self.ai_input.get().strip()
        if not question:
            return
        self.ai_input.delete(0, "end")
        self.add_ai_message("user", question)
        self.status.configure(text="●  Rat AI is thinking...")
        threading.Thread(target=self._request_ai, args=(question,), daemon=True).start()

    def _request_ai(self, question: str):
        source = self.editor.get("1.0", "end-1c")
        system = "You are Rat AI, a helpful programming assistant inside Rat Studio. Rat uses R for variables, printR, ifR, elifR, elseR, whileR, trueR, falseR, and nullR. Explain clearly and provide code when useful."
        payload = {"model": os.getenv("RAT_AI_MODEL", "llama3.2"), "messages": [{"role": "system", "content": system}, {"role": "user", "content": f"Current Rat source:\n```rat\n{source}\n```\n\nQuestion: {question}"}], "stream": False}
        base_url = os.getenv("RAT_AI_BASE_URL", "http://localhost:11434/v1")
        api_key = os.getenv("RAT_AI_API_KEY", "ollama")
        request = urllib.request.Request(f"{base_url.rstrip('/')}/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            answer = data["choices"][0]["message"]["content"]
            self.after(0, self._finish_ai, answer, "●  Rat AI connected")
        except Exception as error:
            if isinstance(error, ConnectionRefusedError) or "10061" in str(error):
                message = "Rat AI is offline because no model server is running.\n\nInstall Ollama, then run:\n\nollama pull llama3.2\nollama serve\n\nKeep that window running and send your question again."
            else:
                message = f"Rat AI could not connect.\n\n{error}\n\nCheck RAT_AI_BASE_URL, RAT_AI_MODEL, and RAT_AI_API_KEY, or use local Ollama."
            self.after(0, self._finish_ai, message, "●  Rat AI offline")

    def _finish_ai(self, answer: str, status: str):
        self.add_ai_message("assistant", answer)
        self.status.configure(text=status)

    def _line_is_foldable(self, line):
        """Return True when a line starts a block with a following indented line."""
        try:
            total = int(self.editor.index("end-1c").split(".")[0])
            if line >= total:
                return False
            current = self.editor.get(f"{line}.0", f"{line}.end")
            nxt = self.editor.get(f"{line + 1}.0", f"{line + 1}.end")
            if not nxt.strip():
                return False
            indent = len(current) - len(current.lstrip(" \t"))
            next_indent = len(nxt) - len(nxt.lstrip(" \t"))
            stripped = current.strip()
            return next_indent > indent and (
                stripped.endswith("{") or
                stripped.endswith(":") or
                stripped.startswith(("ifR ", "elifR ", "elseR", "whileR ",
                                     "infect ", "cheese ", "fang ", "fn ",
                                     "try ", "class "))
            )
        except Exception:
            return False

    def _fold_end(self, start):
        """Find the last indented line belonging to a fold starting at start."""
        total = int(self.editor.index("end-1c").split(".")[0])
        first = self.editor.get(f"{start}.0", f"{start}.end")
        base = len(first) - len(first.lstrip(" \t"))
        end = start
        for line in range(start + 1, total + 1):
            value = self.editor.get(f"{line}.0", f"{line}.end")
            if not value.strip():
                end = line
                continue
            indent = len(value) - len(value.lstrip(" \t"))
            if indent <= base:
                break
            end = line
        return end

    def _fold_click(self, event):
        line = int(self._fold_gutter.index(f"@{event.x},{event.y}").split(".")[0])
        if not self._line_is_foldable(line):
            return
        end = self._fold_end(line)
        if line in self._folds:
            for tag in self._fold_tags.get(line, []):
                try:
                    self.editor.tag_remove(tag, f"{line + 1}.0", f"{end + 1}.0")
                except tk.TclError:
                    pass
            self._folds.remove(line)
            self._fold_tags.pop(line, None)
        else:
            tag = f"rat_fold_{line}"
            self.editor.tag_add(tag, f"{line + 1}.0", f"{end + 1}.0")
            self.editor.tag_configure(tag, elide=True)
            self._folds.add(line)
            self._fold_tags[line] = [tag]
        self._draw_fold_gutter()

    def _draw_fold_gutter(self):
        if not hasattr(self, "_fold_gutter") or not self._fold_gutter.winfo_exists():
            return
        self._fold_gutter.delete("all")
        try:
            first = int(self.editor.index("@0,0").split(".")[0])
            last = int(self.editor.index(f"@0,{self.editor.winfo_height()}").split(".")[0]) + 1
            for line in range(first, last + 1):
                if self._line_is_foldable(line):
                    y = self.editor.dlineinfo(f"{line}.0")
                    if y:
                        yy = y[1] + y[3] // 2
                        symbol = "▶" if line in self._folds else "▼"
                        self._fold_gutter.create_text(11, yy, text=symbol,
                                                      fill="#a8b3aa",
                                                      font=("Segoe UI Symbol", 9))
        except Exception:
            pass

    def _on_editor_change(self):
        self._highlight()
        self._draw_fold_gutter()

    def _highlight(self):
        content = self.editor.get("1.0", "end-1c")
        self.fold_ranges = self.find_fold_ranges(content)
        self.apply_folding()
        for tag in tuple(self.editor.tag_names()):
            if tag.startswith("kw_") or tag in {"string", "comment", "keyword"}:
                self.editor.tag_remove(tag, "1.0", "end")
        keyword_pattern = r"\b(?:" + "|".join(re.escape(k) for k in sorted(self.keyword_colors, key=len, reverse=True)) + r")\b"
        for match in re.finditer(keyword_pattern, content):
            keyword = match.group(0)
            tag = f"kw_{keyword}"
            self.editor.tag_configure(tag, foreground=self.keyword_colors.get(keyword, self.theme["accent"]), font=("Courier New", 10, "bold"))
            self.editor.tag_add(tag, f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')", content):
            self.editor.tag_add("string", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r"--#[^\n]*", content):
            self.editor.tag_add("comment", f"1.0+{match.start()}c", f"1.0+{match.end()}c")

    def find_fold_ranges(self, content):
        ranges = {}
        stack = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            if "{" in line:
                stack.append(line_number)
            if "}" in line and stack:
                start = stack.pop()
                if line_number > start + 1:
                    ranges[start] = line_number
        return ranges

    def apply_folding(self):
        self.editor.tag_remove("folded", "1.0", "end")
        for start, end in self.fold_ranges.items():
            if start in self.collapsed_lines:
                self.editor.tag_add("folded", f"{start + 1}.0", f"{end}.0")
        if hasattr(self, "line_numbers"):
            self.line_numbers.configure(state="normal")
            self.line_numbers.delete("1.0", "end")
            line_count = int(self.editor.index("end-1c").split(".")[0])
            gutter_lines = []
            for line_number in range(1, line_count + 1):
                marker = "-" if line_number in self.fold_ranges and line_number not in self.collapsed_lines else "+" if line_number in self.collapsed_lines else " "
                gutter_lines.append(f"{marker} {line_number}")
            self.line_numbers.insert("1.0", "\n".join(gutter_lines))
            self.line_numbers.configure(state="disabled")

    def toggle_fold(self, event):
        index = self.line_numbers.index(f"@0,{event.y}")
        line_number = int(index.split(".")[0])
        if line_number not in self.fold_ranges and line_number not in self.collapsed_lines:
            return
        if line_number in self.collapsed_lines:
            self.collapsed_lines.remove(line_number)
        else:
            self.collapsed_lines.add(line_number)
        self.apply_folding()

    def expand_all(self):
        self.collapsed_lines.clear()
        self.apply_folding()

    def _show(self, text: str, color="#b8c6c9"):
        self.output.configure(state="normal"); self.output.delete("1.0", "end"); self.output.insert("1.0", text); self.output.tag_configure("result", foreground=color); self.output.tag_add("result", "1.0", "end"); self.output.configure(state="disabled")

    def toggle_translation_view(self):
        if self.translation_view.winfo_ismapped():
            self.translation_view.pack_forget()
            self.output.pack(fill="both", expand=True, padx=2, pady=2)
            return
        self.output.pack_forget()
        self.translation_view.pack(fill="both", expand=True, padx=2, pady=2)
        self.translate()

    def compile_source(self):
        tree = Parser(tokenize(self.editor.get("1.0", "end-1c"))).program(); compiler = Compiler(); compiler.compile(tree); return compiler.code

    def compile(self):
        try:
            code = self.compile_source(); self._show(f"Compiled successfully.\n{len(code)} bytecode instructions generated.", "#67d39a"); self.status.configure(text="Compiled  |  no errors")
            self.clear_error_line()
            return code
        except (RatError, RuntimeError) as error:
            self.show_error(error, "Compile failed  |  fix the error and try again"); return None

    def run(self):
        code = self.compile()
        if code is not None:
            self.stop_requested = False
            self.status.configure(text="Running...  |  Rat VM")
            threading.Thread(target=self.run_code, args=(code,), daemon=True).start()

    def run_code(self, code):
        try:
            output = "\n".join(execute(code, lambda: self.stop_requested)) or "Program finished with no output."
            self.after(0, self._finish_run, output, "Ran successfully  |  Rat VM", "#d6e0e1")
        except Exception as error:
            status = "Stopped  |  Rat VM" if self.stop_requested else "Runtime error"
            self.after(0, self._finish_run, str(error), status, "#ee8d76")

    def _finish_run(self, output, status, color):
        self._show(output, color)
        self.status.configure(text=status)

    def stop(self):
        self.stop_requested = True
        self.status.configure(text="Stopping...  |  Rat VM")

    def show_error(self, error, status):
        self._show(str(error), "#ee8d76")
        self.last_error_line = getattr(error, "line", None)
        if self.last_error_line:
            self.highlight_error_line(self.last_error_line)
        self.status.configure(text=status)

    def clear_error_line(self):
        self.editor.tag_remove("error_line", "1.0", "end")
        self.last_error_line = None

    def highlight_error_line(self, line):
        self.clear_error_line()
        self.editor.tag_add("error_line", f"{line}.0", f"{line}.end+1c")
        self.editor.see(f"{line}.0")

    def translate(self):
        try:
            target = self.translation_target.get()
            translated = translate_source(self.editor.get("1.0", "end-1c"), target)
            self.translation_view.configure(state="normal")
            self.translation_view.delete("1.0", "end")
            self.translation_view.insert("1.0", translated)
            self.translation_view.configure(state="disabled")
            self._show(f"# Rat AI translation -> {target}\n\n{translated}", "#d6e0d6")
            self.target_status.configure(text=f"Target: {target}")
            self.status.configure(text=f"Translated  |  Rat AI -> {target}")
        except (RatError, RuntimeError) as error:
            self._show(f"Rat AI could not translate this program.\n{error}", "#ee8d76")
            self.status.configure(text="Translation failed  |  fix the Rat source and try again")

    def new_file(self, _event=None):
        """Create a new unsaved document; do not save it automatically."""
        self.new_tab()
        return "break" if _event is not None else None

    def open_file(self):
        filename = filedialog.askopenfilename(filetypes=[("Rat files", "*.rat"), ("All files", "*.*")])
        if filename:
            self.open_path_in_tab(Path(filename))
            self.status.configure(text=f"●  Opened  |  {self.current_file.name}")

    def save_as(self, _event=None):
        """Save the current tab under a new filename without losing the tab."""
        filename = filedialog.asksaveasfilename(
            defaultextension=".rat",
            filetypes=[("Rat files", "*.rat"), ("All files", "*.*")],
            initialdir=str(self.project_root),
            initialfile=self.active_tab if self.active_tab.endswith(".rat") else "untitled.rat",
            parent=self,
        )
        if not filename:
            return False

        path = Path(filename)
        if path.suffix.lower() != ".rat":
            path = path.with_suffix(".rat")

        path.parent.mkdir(parents=True, exist_ok=True)
        content = self.editor.get("1.0", "end-1c")
        path.write_text(content, encoding="utf-8")

        old_name = self.active_tab
        display_name = path.name

        # If another open tab already uses this display name, keep both tabs.
        if display_name != old_name and display_name in self.tabs:
            stem, suffix = path.stem, path.suffix
            n = 2
            while f"{stem}-{n}{suffix}" in self.tabs:
                n += 1
            display_name = f"{stem}-{n}{suffix}"

        tab = self.tabs.pop(old_name)
        tab["path"] = path
        tab["content"] = content
        tab["dirty"] = False
        self.tabs[display_name] = tab

        self.active_tab = display_name
        self.current_file = path
        try:
            self.editor.edit_modified(False)
        except tk.TclError:
            pass

        self.refresh_tabs()
        self.refresh_project_files()
        self.editor_header_label.configure(text=f" RAT SOURCE   |   {display_name}")
        self.status.configure(text=f"●  Saved As  |  {path.name}")
        return True

    def save_all(self):
        self.store_active_tab()
        saved = 0
        for name, tab in list(self.tabs.items()):
            path = tab.get("path")
            content = tab.get("content", "")
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(content, encoding="utf-8")
                saved += 1
        if self.current_file is None and self.editor.get("1.0", "end-1c").strip():
            self.save_file()
            saved += 1
        self.refresh_project_files()
        self.status.configure(text=f"●  Save All  |  {saved} file(s) saved")

    def find_replace(self, _event=None):
        if getattr(self, "find_window", None) is not None:
            try:
                self.find_window.lift()
                self.find_entry.focus_set()
                return "break"
            except tk.TclError:
                self.find_window = None
        win = tk.Toplevel(self)
        self.find_window = win
        win.title("Find / Replace — Rat Studio")
        win.geometry("440x175")
        win.resizable(False, False)
        win.configure(bg="#c0c0c0")
        win.transient(self)

        frame = tk.Frame(win, bg="#c0c0c0")
        frame.pack(fill="both", expand=True, padx=12, pady=10)
        tk.Label(frame, text="Find:", bg="#c0c0c0", font=("MS Sans Serif", 9)).grid(row=0,column=0,sticky="w",pady=4)
        self.find_entry = tk.Entry(frame, width=38)
        self.find_entry.grid(row=0,column=1,pady=4)
        tk.Label(frame, text="Replace:", bg="#c0c0c0", font=("MS Sans Serif", 9)).grid(row=1,column=0,sticky="w",pady=4)
        replace_entry = tk.Entry(frame, width=38)
        replace_entry.grid(row=1,column=1,pady=4)

        def find_next():
            needle=self.find_entry.get()
            if not needle: return
            start=self.editor.index("insert")
            pos=self.editor.search(needle,start,stopindex="end",nocase=False)
            if not pos:
                pos=self.editor.search(needle,"1.0",stopindex="end",nocase=False)
            self.editor.tag_remove("find_match","1.0","end")
            if pos:
                end=f"{pos}+{len(needle)}c"
                self.editor.tag_add("find_match",pos,end)
                self.editor.tag_configure("find_match",background="#ffff80",foreground="#000000")
                self.editor.mark_set("insert",end)
                self.editor.see(pos)

        def replace_one():
            if self.editor.tag_ranges("find_match"):
                a,b=self.editor.tag_ranges("find_match")
                self.editor.delete(a,b)
                self.editor.insert(a,replace_entry.get())
                self.editor_changed(); self._highlight()
            find_next()

        def replace_all():
            needle=self.find_entry.get()
            replacement=replace_entry.get()
            if not needle: return
            content=self.editor.get("1.0","end-1c")
            count=content.count(needle)
            self.editor.delete("1.0","end")
            self.editor.insert("1.0",content.replace(needle,replacement))
            self.editor_changed(); self._highlight()
            self.status.configure(text=f"●  Replaced {count} occurrence(s)")

        buttons=tk.Frame(frame,bg="#c0c0c0")
        buttons.grid(row=2,column=1,sticky="e",pady=8)
        for label,cmd in (("Find Next",find_next),("Replace",replace_one),("Replace All",replace_all)):
            tk.Button(buttons,text=label,command=cmd,bg="#c0c0c0",relief="raised",bd=2).pack(side="left",padx=2)
        tk.Button(buttons,text="Close",command=win.destroy,bg="#c0c0c0",relief="raised",bd=2).pack(side="left",padx=2)
        win.protocol("WM_DELETE_WINDOW",win.destroy)
        self.find_entry.focus_set()
        return "break"

    def go_to_line(self, _event=None):
        value=simpledialog.askinteger("Go to Line","Line number:",minvalue=1,parent=self)
        if value:
            index=f"{value}.0"
            self.editor.mark_set("insert",index)
            self.editor.see(index)
            self.update_cursor_status()
        return "break"

    def change_editor_font(self, delta, reset=False):
        if reset:
            size=10
        else:
            current=getattr(self,"editor_font_size",10)
            size=max(7,min(24,current+delta))
        self.editor_font_size=size
        self.editor.configure(font=("Courier New",size))
        self.line_numbers.configure(font=("Courier New",size))
        self.status.configure(text=f"●  Editor zoom  |  {size}px")

    def toggle_word_wrap(self):
        current=self.editor.cget("wrap")
        new="word" if current=="none" else "none"
        self.editor.configure(wrap=new)
        self.status.configure(text=f"●  Word wrap  |  {'ON' if new=='word' else 'OFF'}")

    def show_shortcuts(self):
        messagebox.showinfo("Rat Studio Shortcuts",
            "Ctrl+N  New tab\nCtrl+O  Open\nCtrl+S  Save\nCtrl+Shift+S  Save As\n"
            "Ctrl+F  Find / Replace\nCtrl+G  Go to line\nCtrl+W  Close tab\nF5  Run Rat\n"
            "Ctrl+Shift+U  New tab\nCtrl+Q  Rat Notepad", parent=self)

    def show_about(self):
        messagebox.showinfo("About Rat Studio",
            f"Rat Studio v{APP_VERSION}\n\nA Windows-classic IDE for the Rat programming language.\n"
            "Built-in compiler, Rat VM, project files, syntax highlighting, Rat AI, and tutorials.", parent=self)

    def save_file(self, _event=None):
        """Save the active tab. New tabs use Save As and remain separate tabs."""
        self.store_active_tab()
        tab = self.tabs.get(self.active_tab)
        if tab is None:
            self.new_tab()
            tab = self.tabs[self.active_tab]

        path = tab.get("path")
        if path is None:
            return self.save_as(_event)

        path = Path(path)
        content = self.editor.get("1.0", "end-1c")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        tab["path"] = path
        tab["content"] = content
        tab["dirty"] = False
        self.current_file = path
        try:
            self.editor.edit_modified(False)
        except tk.TclError:
            pass

        self.refresh_tabs()
        self.refresh_project_files()
        self.status.configure(text=f"●  Saved  |  {path.name}")
        return True

if __name__ == "__main__":
    app = RatStudio()
    app.mainloop()

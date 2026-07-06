#!/usr/bin/env python3
"""rtt-cli — interactive menu + wizard + chat front-end for RTT.

Reads config_schema.py for EVERYTHING (fields, profiles, validation, launch
command). No option is hardcoded here — add a Field to the schema and it flows
into this UI automatically.

Three ways to drive it:
  1. Main menu      → pick a profile, tweak, launch.
  2. Wizard         → walk every applicable option group by group.
  3. Chat           → natural language ("use hebrew", "chunk 8", "add my mic").

Pure stdlib (input/print). No third-party deps — works over SSH, no install.

Usage:
    rtt-cli                 # interactive main menu
    rtt-cli --wizard        # jump straight into the wizard
    rtt-cli --chat          # jump straight into chat mode
    rtt-cli --profile rtt   # load a profile and go to the action menu
    rtt-cli --print-argv --profile rttheb   # non-interactive: print launch args
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config_schema as schema  # noqa: E402

CONFIG_DIR = Path(os.environ.get("RTT_CLI_CONFIG_DIR", Path.home() / ".config" / "rtt-cli"))
TRANSCRIBE = HERE / "transcribe.py"
PYTHON = str(HERE / ".venv" / "bin" / "python")
if not Path(PYTHON).exists():
    PYTHON = sys.executable


# --------------------------------------------------------------------------
# Tiny ANSI helpers (no dependency)
# --------------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s


def bold(s: str) -> str: return _c("1", s)
def dim(s: str) -> str: return _c("2", s)
def cyan(s: str) -> str: return _c("36", s)
def green(s: str) -> str: return _c("32", s)
def yellow(s: str) -> str: return _c("33", s)
def red(s: str) -> str: return _c("31", s)


def hr() -> None:
    print(dim("─" * 60))


def banner() -> None:
    print()
    print(bold(cyan("  RTT-CLI")) + dim("  ·  real-time transcriber control"))
    hr()


def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" {dim('[' + default + ']')}" if default else ""
    try:
        raw = input(f"{prompt}{suffix} {cyan('›')} ").strip()
    except EOFError:
        return default or ""
    return raw if raw else (default or "")


def ask_yn(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    raw = ask(f"{prompt} {dim('(' + d + ')')}").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true", "on")


def choose(prompt: str, options: List[str], helps: Optional[Dict[str, str]] = None,
           default: Optional[str] = None) -> Optional[str]:
    """Numbered picker. Returns the chosen option string, or None if aborted."""
    print(bold(prompt))
    for i, opt in enumerate(options, 1):
        h = f"  {dim('— ' + helps[opt])}" if helps and opt in helps else ""
        marker = green(" (current)") if opt == default else ""
        print(f"  {cyan(str(i))}. {opt}{marker}{h}")
    raw = ask("Pick a number (or blank to keep/cancel)", None)
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    # allow typing the value directly
    if raw in options:
        return raw
    print(red(f"  '{raw}' not a valid choice"))
    return choose(prompt, options, helps, default)


# --------------------------------------------------------------------------
# Config persistence
# --------------------------------------------------------------------------

def save_config(cfg: Dict[str, Any], name: str) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / f"{name}.json"
    path.write_text(json.dumps(cfg, indent=2))
    return path


def load_config(name: str) -> Optional[Dict[str, Any]]:
    path = CONFIG_DIR / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def list_saved() -> List[str]:
    if not CONFIG_DIR.exists():
        return []
    return sorted(p.stem for p in CONFIG_DIR.glob("*.json"))


# --------------------------------------------------------------------------
# Review + launch
# --------------------------------------------------------------------------

def review(cfg: Dict[str, Any]) -> None:
    hr()
    print(bold("Current configuration"))
    last_group = None
    for group, name, display in schema.summarize_config(cfg):
        if group != last_group:
            print(f"\n  {yellow(schema.GROUP_LABELS.get(group, group))}")
            last_group = group
        print(f"    {name:22} {display}")
    errs = schema.validate_config(cfg)
    if errs:
        print("\n" + red(bold("  ⚠ validation:")))
        for e in errs:
            print(red(f"    - {e}"))
    hr()


def launch_command(cfg: Dict[str, Any]) -> List[str]:
    return [PYTHON, str(TRANSCRIBE)] + schema.build_argv(cfg)


def do_launch(cfg: Dict[str, Any]) -> None:
    errs = schema.validate_config(cfg)
    if errs:
        print(red("Cannot launch — fix these first:"))
        for e in errs:
            print(red(f"  - {e}"))
        return
    cmd = launch_command(cfg)
    print(green("\nLaunching:"))
    print(dim("  " + " ".join(cmd)))
    hr()
    try:
        subprocess.run(cmd, cwd=str(HERE))
    except KeyboardInterrupt:
        print(dim("\n(stopped)"))


# --------------------------------------------------------------------------
# Wizard mode — walk every applicable field, group by group.
# --------------------------------------------------------------------------

def edit_field(cfg: Dict[str, Any], f: schema.Field) -> None:
    current = cfg.get(f.name)
    cur_disp = "***" if (f.secret and current) else (str(current) if current is not None else "(unset)")
    print(f"\n{bold(f.name)} {dim('(' + f.flag + ')')}")
    print(dim("  " + f.help))
    print(f"  current: {cyan(cur_disp)}")

    if f.type == "bool":
        cfg[f.name] = ask_yn("  enable?", bool(current))
        return
    if f.type == "choice" and f.choices:
        picked = choose("  choose:", f.choices, default=str(current) if current else None)
        if picked is not None:
            cfg[f.name] = picked
        return
    raw = ask("  value (blank=keep, '-'=clear)", None)
    if raw == "-":
        cfg[f.name] = None
    elif raw:
        try:
            cfg[f.name] = f.coerce(raw)
            err = f.validate(cfg[f.name])
            if err:
                print(red("  " + err))
                cfg[f.name] = current
            elif f.name == "input_file" and cfg.get(f.name):
                # File mode conflicts with live capture — clear the device so the
                # generated command isn't rejected by transcribe.py's guard.
                if cfg.get("input_device"):
                    cfg["input_device"] = None
                    print(dim("  (cleared input_device — file mode overrides live capture)"))
        except (ValueError, TypeError):
            print(red(f"  invalid {f.type}: {raw}"))


def wizard(cfg: Dict[str, Any]) -> Dict[str, Any]:
    banner()
    print(bold("Wizard") + dim(" — walk through every option. Blank keeps the current value."))

    backend = choose("\nBackend:", schema.BACKENDS, schema.BACKEND_HELP,
                     default=cfg.get("backend", "openai"))
    if backend:
        # switching backend resets to that backend's defaults, preserving shared keys
        shared = {k: v for k, v in cfg.items()
                  if (fld := schema.field_by_name(k)) and fld.applies(backend)}
        cfg = schema.default_config(backend)
        cfg.update(shared)
        cfg["backend"] = backend

    for group in schema.GROUPS:
        if group == "backend":
            continue
        fields = schema.fields_in_group(group, cfg["backend"])
        if not fields:
            continue
        print(f"\n{yellow(bold(schema.GROUP_LABELS.get(group, group)))}")
        if not ask_yn(f"  configure {len(fields)} {group} option(s)?", False):
            continue
        for f in fields:
            edit_field(cfg, f)

    review(cfg)
    return cfg


# --------------------------------------------------------------------------
# Chat mode — natural-language config.
# --------------------------------------------------------------------------

CHAT_HELP = """
Chat mode — tell me what you want in plain language. Examples:
  • "use hebrew"                       → llmspeech + he-IL locales
  • "switch to local whisper"          → backend local
  • "chunk 8"  /  "faster"             → chunk-seconds 8
  • "add my mic"                       → include-mic on
  • "save to notes.txt"                → output file
  • "use the classic backend"          → azure
  • "show" / "review"                  → print current config
  • "launch" / "go"                    → start transcription
  • "wizard"                           → switch to the full wizard
  • "help"                             → this text
  • "quit"                             → exit
"""


def chat_apply(cfg: Dict[str, Any], text: str) -> Optional[str]:
    """Interpret one NL line. Returns a status message, or None if unhandled.

    Deterministic keyword parser (no LLM) driven by field aliases in the schema.
    """
    t = text.strip().lower()
    if not t:
        return None

    # --- intents / verbs ---
    if t in ("show", "review", "status", "config"):
        review(cfg)
        return ""
    if t in ("launch", "go", "start", "run"):
        return "__launch__"
    if t in ("wizard", "menu"):
        return "__wizard__"
    if t in ("help", "?"):
        print(CHAT_HELP)
        return ""
    if t in ("quit", "exit", "q", "done"):
        return "__quit__"

    # --- backend shortcuts by name / synonym ---
    backend_words = {
        "local": "local", "whisper": "local", "offline": "local",
        "openai": "openai", "gpt": "openai", "diarize": "openai",
        "llmspeech": "llmspeech", "llm speech": "llmspeech",
        "azure": "azure", "classic": "azure", "speech sdk": "azure", "old": "azure",
    }
    for word, backend in backend_words.items():
        if word in t and ("backend" in t or "use" in t or "switch" in t or word in ("classic", "offline")):
            _switch_backend(cfg, backend)
            return f"backend → {backend}"

    # --- special phrase: hebrew ---
    if "hebrew" in t or "he-il" in t or "עברית" in t:
        _switch_backend(cfg, "llmspeech")
        cfg["llmspeech_locales"] = "en-US,he-IL"
        return "backend → llmspeech, locales → en-US,he-IL (Hebrew/English)"

    # --- faster / slower (latency shortcuts on chunk_seconds) ---
    if cfg["backend"] in ("openai", "llmspeech"):
        if "faster" in t or "more live" in t or "less lag" in t or "lower latency" in t:
            cfg = cfg
            cur = float(cfg.get("chunk_seconds") or 10)
            cfg["chunk_seconds"] = max(3.0, cur - 3)
            return f"chunk_seconds → {cfg['chunk_seconds']} (more live)"
        if "slower" in t or "more accurate" in t or "higher accuracy" in t:
            cur = float(cfg.get("chunk_seconds") or 10)
            cfg["chunk_seconds"] = min(60.0, cur + 5)
            return f"chunk_seconds → {cfg['chunk_seconds']} (more accurate)"

    # --- generic: match a field by alias, then pull a value from the text ---
    matched = _match_field(cfg["backend"], t)
    if matched is not None:
        return _apply_value(cfg, matched, t)

    return None


def _switch_backend(cfg: Dict[str, Any], backend: str) -> None:
    shared = {k: v for k, v in cfg.items()
              if (fld := schema.field_by_name(k)) and k != "backend" and fld.applies(backend)}
    new = schema.default_config(backend)
    new.update(shared)
    new["backend"] = backend
    cfg.clear()
    cfg.update(new)


def _match_field(backend: str, text: str) -> Optional[schema.Field]:
    """Find the field whose alias best matches the text (longest alias wins)."""
    best: Optional[schema.Field] = None
    best_len = 0
    for f in schema.fields_for_backend(backend):
        for alias in f.aliases:
            if alias in text and len(alias) > best_len:
                best, best_len = f, len(alias)
    return best


def _apply_value(cfg: Dict[str, Any], f: schema.Field, text: str) -> str:
    """Pull a value for field f out of the NL text and apply it."""
    import re

    if f.type == "bool":
        off = any(w in text for w in ("no ", "off", "disable", "without", "remove", "don't"))
        cfg[f.name] = not off
        return f"{f.name} → {cfg[f.name]}"

    if f.type in ("int", "float"):
        m = re.search(r"(-?\d+(?:\.\d+)?)", text)
        if m:
            val = f.coerce(m.group(1))
            err = f.validate(val)
            if err:
                return red(err)
            cfg[f.name] = val
            return f"{f.name} → {val}"
        return f"({f.name}: tell me a number, e.g. '{f.aliases[0]} 8')"

    if f.type == "choice" and f.choices:
        for c in f.choices:
            if c.lower() in text:
                cfg[f.name] = c
                return f"{f.name} → {c}"
        return f"({f.name}: choose one of {f.choices})"

    # str/path: take the tail after the alias keyword or after 'to'/'='
    m = re.search(r"(?:to|=|:)\s*(\S.+)$", text)
    if m:
        cfg[f.name] = m.group(1).strip().strip("'\"")
        return f"{f.name} → {cfg[f.name]}"
    return f"({f.name}: say '{f.aliases[0]} to <value>')"


def chat(cfg: Dict[str, Any]) -> Dict[str, Any]:
    banner()
    print(bold("Chat mode") + dim(" — plain language. Type 'help' for examples, 'go' to launch."))
    print(dim(f"  backend: {cfg['backend']}"))
    while True:
        try:
            line = input(f"\n{cyan('rtt')} {cyan('›')} ").strip()
        except EOFError:
            break
        if not line:
            continue
        result = chat_apply(cfg, line)
        if result == "__quit__":
            break
        if result == "__launch__":
            do_launch(cfg)
            continue
        if result == "__wizard__":
            cfg = wizard(cfg)
            continue
        if result is None:
            print(yellow(f"  didn't catch that — try 'help'. (backend={cfg['backend']})"))
        elif result:
            print(green("  ✓ " + result))
    return cfg


# --------------------------------------------------------------------------
# Main menu
# --------------------------------------------------------------------------

def main_menu(cfg: Dict[str, Any]) -> None:
    while True:
        banner()
        print(f"  backend: {green(cfg['backend'])}   " + dim("(profile-based; tweak below)"))
        print()
        actions = [
            "Launch now",
            "Wizard (walk every option)",
            "Chat (natural language)",
            "Pick a profile",
            "Edit one setting",
            "Review config",
            "Save config",
            "Load saved config",
            "Quit",
        ]
        for i, a in enumerate(actions, 1):
            print(f"  {cyan(str(i))}. {a}")
        pick = ask("\nChoose", None)

        if pick in ("1", "launch", "go"):
            do_launch(cfg)
        elif pick in ("2", "wizard"):
            cfg = wizard(cfg)
        elif pick in ("3", "chat"):
            cfg = chat(cfg)
        elif pick in ("4", "profile"):
            _pick_profile(cfg)
        elif pick == "5":
            _edit_one(cfg)
        elif pick in ("6", "review"):
            review(cfg)
        elif pick == "7":
            name = ask("Save as", "myconfig")
            p = save_config(cfg, name)
            print(green(f"  saved → {p}"))
        elif pick == "8":
            saved = list_saved()
            if not saved:
                print(yellow("  no saved configs"))
            else:
                name = choose("Load which?", saved)
                if name:
                    loaded = load_config(name)
                    if loaded:
                        cfg.clear()
                        cfg.update(loaded)
                        print(green(f"  loaded {name}"))
        elif pick in ("9", "quit", "q", "exit"):
            print(dim("bye"))
            return


def _pick_profile(cfg: Dict[str, Any]) -> None:
    names = list(schema.PROFILES.keys())
    picked = choose("Profile:", names, schema.PROFILE_HELP, default=None)
    if picked:
        new = schema.resolve_profile(picked)
        cfg.clear()
        cfg.update(new)
        print(green(f"  → {picked}: {schema.PROFILE_HELP.get(picked, '')}"))


def _edit_one(cfg: Dict[str, Any]) -> None:
    fields = schema.fields_for_backend(cfg["backend"])
    names = [f.name for f in fields]
    picked = choose("Which setting?", names)
    if picked:
        f = schema.field_by_name(picked)
        if f:
            edit_field(cfg, f)


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------

def build_initial_config(args) -> Dict[str, Any]:
    if args.profile:
        try:
            return schema.resolve_profile(args.profile)
        except KeyError:
            print(red(f"unknown profile '{args.profile}'. Known: {list(schema.PROFILES)}"))
            sys.exit(2)
    if args.load:
        loaded = load_config(args.load)
        if loaded:
            return loaded
        print(red(f"no saved config '{args.load}'"))
        sys.exit(2)
    return schema.resolve_profile("rtt")  # sensible default


def main() -> None:
    ap = argparse.ArgumentParser(prog="rtt-cli", description="Interactive control for RTT.")
    ap.add_argument("--wizard", action="store_true", help="Jump into the wizard.")
    ap.add_argument("--chat", action="store_true", help="Jump into chat mode.")
    ap.add_argument("--profile", help=f"Start from a profile: {list(schema.PROFILES)}")
    ap.add_argument("--load", help="Start from a saved config name.")
    ap.add_argument("--print-argv", action="store_true", help="Print transcribe.py args and exit.")
    ap.add_argument("--launch", action="store_true", help="Validate + launch immediately, no menu.")
    args = ap.parse_args()

    cfg = build_initial_config(args)

    if args.print_argv:
        print(" ".join(schema.build_argv(cfg)))
        return
    if args.launch:
        do_launch(cfg)
        return
    if args.wizard:
        cfg = wizard(cfg)
        if ask_yn("\nLaunch now?", True):
            do_launch(cfg)
        return
    if args.chat:
        chat(cfg)
        return

    main_menu(cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(dim("\n(interrupted)"))
        sys.exit(130)

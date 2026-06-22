#!/usr/bin/env python3
"""
hermes-xiaoai-bridge / script / monitor.py
常驻后台进程（Flow A）。轮询小爱对话，检测到新记录时 POST 到 Hermes webhook，
并更新 monitor_state.json 游标。

日志写 stderr + {workspace}/monitor.log。

唯一实例保证：fcntl.flock 文件锁，进程退出时 OS 自动释放。
"""
from __future__ import annotations

import argparse
import atexit
import fcntl
import json
import logging
import logging.handlers
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import requests
from mijiaAPI import mijiaAPI as MijiaAPI

_lock_fd: int | None = None


# ---------------------------------------------------------------------------
def workspace_path(workspace: str, *parts: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(workspace))).joinpath(*parts)


# ---------------------------------------------------------------------------
def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    default = Path.home() / ".config" / "hermes-xiaoai-bridge" / "config.json"
    p = Path(os.path.expandvars(os.path.expanduser(str(path or default))))
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
def setup_logging(workspace: str, verbose: bool = False, level: str | None = None) -> logging.Logger:
    if level:
        log_level = getattr(logging, level.upper(), logging.INFO)
    elif verbose:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    root = logging.getLogger()
    if not root.handlers:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        root.addHandler(stderr_handler)

        log_file = workspace_path(workspace, "monitor.log")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    root.setLevel(log_level)
    return logging.getLogger("bridge")


# ---------------------------------------------------------------------------
def load_auth(workspace: str) -> Dict[str, Any]:
    path = workspace_path(workspace, "auth.json")
    if not path.exists():
        raise FileNotFoundError(f"auth.json not found at {path}; run: mijiaAPI login -p {path}")
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if "passToken" not in raw:
        raise ValueError("auth.json missing passToken; re-login via mijiaAPI login")
    return raw


# ---------------------------------------------------------------------------
def get_conversations(api: MijiaAPI, did: str, limit: int = 2) -> List[Dict[str, Any]]:
    try:
        raw = api.get_xiaoai_conversations(device_id=did, limit=limit)
    except RuntimeError as e:
        raise RuntimeError(str(e)) from e

    result: List[Dict[str, Any]] = []
    for conv in raw:
        result.append({
            "time": conv.get("timestamp_ms", 0),
            "query": conv.get("query", ""),
            "answer": conv.get("answer", ""),
            "requestId": conv.get("requestId"),
        })
    return result


def list_wifispeakers(api: MijiaAPI) -> List[Dict[str, str]]:
    devices = api.list_xiaoai_devices()
    result: List[Dict[str, str]] = []
    # Known wifispeaker hardware codes (from mijiaAPI list output)
    known_ws_hardware = {"l16a", "l06a", "a10a", "a08a", "x08a", "x10a", "x12a", "p10a", "t10a"}
    for d in devices:
        model = (d.get("hardware") or d.get("model") or "").lower()
        # Check if model field contains "wifispeaker" OR hardware is a known wifispeaker code
        is_ws = "wifispeaker" in model or model in known_ws_hardware
        if not is_ws:
            continue
        result.append({
            "name": str(d.get("name", "")),
            "did": str(d.get("miotDID") or d.get("deviceID") or ""),
            "model": str(d.get("hardware") or d.get("model") or ""),
        })
    return result


def resolve_speakers(api: MijiaAPI, names: list[str]) -> list[tuple[str, str]]:
    all_devices = list_wifispeakers(api)
    name_to_did = {d["name"]: d["did"] for d in all_devices}

    result: list[tuple[str, str]] = []
    for name in names:
        if name not in name_to_did:
            raise ValueError(f"unknown speaker '{name}'; known: {list(name_to_did)}")
        result.append((name, name_to_did[name]))

    if not result:
        raise ValueError("no wifispeakers found on this account")

    return result


# ---------------------------------------------------------------------------
def claim_lock(workspace: str) -> None:
    global _lock_fd
    lock_file = workspace_path(workspace, "monitor.lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    _lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"Another monitor instance is already running (lock held by PID "
              f"{open(lock_file).read().strip()})", file=sys.stderr)
        os.close(_lock_fd)
        sys.exit(1)

    os.ftruncate(_lock_fd, 0)
    os.write(_lock_fd, str(os.getpid()).encode())


def cleanup_lock(workspace: str) -> None:
    global _lock_fd
    if _lock_fd is not None:
        try:
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None
    lock_file = workspace_path(workspace, "monitor.lock")
    try:
        if lock_file.exists():
            lock_file.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
def read_state(workspace: str) -> dict:
    sf = workspace_path(workspace, "monitor_state.json")
    if sf.exists():
        try:
            return json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def write_state(workspace: str, state: dict) -> None:
    sf = workspace_path(workspace, "monitor_state.json")
    sf.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
def _answer_incomplete(answer: str | None) -> bool:
    text = (answer or "").strip()
    return not text or text in ("...", "…")


def poll_speaker(
    api: MijiaAPI,
    did: str,
    speaker: str,
    workspace: str,
    state: dict,
    log: logging.Logger,
) -> tuple[list[dict], str | None]:
    """轮询音箱对话，返回 (新记录列表, 最新 requestId)。
    
    注意：state 更新由调用方在 webhook POST 成功后执行，避免重复 POST。
    """
    records = get_conversations(api, did, limit=2)
    sk = f"latest_requestId::{speaker}"
    latest_requestId = state.get(sk)

    if not latest_requestId:
        for rec in records:
            if _answer_incomplete(rec.get("answer")):
                continue
            new_rid = str(rec.get("requestId", ""))
            log.info("%s: baseline set to requestId=%s", speaker, new_rid)
            return [], new_rid  # 不更新 state，等待 POST 成功后再更新

    new_records: list[dict] = []
    seen: set[str] = set()
    for rec in records:
        rid = str(rec.get("requestId", ""))
        if not rid or rid in seen:
            continue
        seen.add(rid)
        if rid == latest_requestId:
            break
        if _answer_incomplete(rec.get("answer")):
            continue
        new_records.append(rec)

    new_rid = str(new_records[0].get("requestId", "")) if new_records else None
    if new_rid:
        log.debug("%s: new requestId=%s (latest was %s)", speaker, new_rid, latest_requestId)
    
    return new_records, new_rid


# ---------------------------------------------------------------------------
def main(
    once: bool = False,
    speaker_arg: str | None = None,
    interval: int = 1,
    verbose: bool = False,
) -> int:
    cfg = load_config()
    workspace = cfg["workspace"]
    monitor_cfg = cfg.get("monitor", {})

    log = setup_logging(workspace, verbose=verbose, level=cfg.get("log_level", "INFO"))
    log.info("workspace: %s, log_level: %s", workspace, logging.getLevelName(logging.getLogger().level))

    if not monitor_cfg.get("enabled", True):
        log.info("monitor.enabled is false, exiting")
        return 0

    claim_lock(workspace)
    atexit.register(cleanup_lock, workspace)

    auth_file = str(workspace_path(workspace, "auth.json"))
    auth_data = load_auth(workspace)
    api = MijiaAPI(auth_file)
    api.auth_data = auth_data

    if speaker_arg == "all":
        names = list({d["name"] for d in list_wifispeakers(api)})
    elif speaker_arg:
        names = [s.strip() for s in speaker_arg.split(",")]
    else:
        names = monitor_cfg.get("wifispeakers", [])

    if not names:
        log.error("no speakers to monitor — set monitor.wifispeakers in config.json or use --speaker")
        return 1

    speaker_list = resolve_speakers(api, names)
    log.info("monitoring %d speaker(s): %s", len(speaker_list), [s[0] for s in speaker_list])

    state = read_state(workspace)

    for name, did in speaker_list:
        if f"latest_requestId::{name}" not in state:
            log.info("first run, setting baseline for %s", name)
            _, new_rid = poll_speaker(api, did, name, workspace, state, log)
            if new_rid:
                state[f"latest_requestId::{name}"] = new_rid
                write_state(workspace, state)
                log.info("%s: baseline saved to state (requestId=%s)", name, new_rid)
            if once:
                return 0

    if once:
        for name, did in speaker_list:
            poll_speaker(api, did, name, workspace, state, log)
        return 0

    interval = monitor_cfg.get("poll_interval", interval)
    webhook_url = monitor_cfg.get("webhook", "")
    if not webhook_url:
        log.error("monitor.webhook not set in config.json")
        return 1

    log.info("starting poll loop (interval=%ds), webhook=%s", interval, webhook_url)
    quiet_ticks = 0
    post_count = 0
    MAX_RETRIES = 3
    while True:
        quiet_ticks += 1
        try:
            for name, did in speaker_list:
                new_records, new_rid = poll_speaker(api, did, name, workspace, state, log)
                if new_records:
                    quiet_ticks = 0
                    log.info("%s: %d new conversation(s)", name, len(new_records))
                    rec = new_records[0]
                    if _answer_incomplete(rec.get("answer")):
                        log.info("%s: skip POST, answer still incomplete", name)
                        continue
                    payload = {
                        "speaker": name,
                        "query": rec.get("query", ""),
                        "answer": rec.get("answer", ""),
                        "requestId": rec.get("requestId", ""),
                        "trace_id": str(uuid.uuid4())[:8],
                    }
                    log.info("%s: POST webhook trace_id=%s requestId=%s (post#%d)",
                             name, payload["trace_id"], payload["requestId"], post_count)
                    post_count += 1
                    post_ok = False
                    try:
                        resp = requests.post(webhook_url, json=payload, timeout=3)
                        if resp.status_code >= 400:
                            log.warning("webhook POST failed: %s %s", resp.status_code, resp.text[:100])
                        else:
                            log.info("webhook POST ok: %s", resp.status_code)
                            post_ok = True
                    except requests.RequestException as e:
                        log.warning("webhook POST error: %s", e)

                    if post_ok:
                        if new_rid:
                            state[f"latest_requestId::{name}"] = new_rid
                            state.pop(f"_retries::{name}", None)
                            write_state(workspace, state)
                            log.debug("%s: state updated to requestId=%s", name, new_rid)
                    else:
                        retries_key = f"_retries::{name}"
                        retries = state.get(retries_key, 0) + 1
                        rid_for_state = new_rid or str(rec.get("requestId", ""))
                        if retries >= MAX_RETRIES:
                            log.warning("%s: requestId=%s failed %d times, marking as processed",
                                        name, rid_for_state, retries)
                            state[f"latest_requestId::{name}"] = rid_for_state
                            state.pop(retries_key, None)
                            write_state(workspace, state)
                        else:
                            state[retries_key] = retries
                            write_state(workspace, state)
                            log.info("%s: requestId=%s retry %d/%d", name, rid_for_state, retries, MAX_RETRIES)
        except Exception as e:
            log.warning("poll error: %s", e)

        if quiet_ticks > 0 and quiet_ticks % 60 == 0:
            log.info("heartbeat: monitoring %d speaker(s), last active %d polls ago",
                     len(speaker_list), quiet_ticks)

        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="小爱音箱监听守护脚本")
    parser.add_argument(
        "--speaker", default=None,
        help='音箱米家名称（如"卧室小爱"），多个用逗号分隔，或 --speaker all 监听全部',
    )
    parser.add_argument("--once", action="store_true", help="单次模式")
    parser.add_argument("--interval", type=int, default=1, help="轮询间隔秒数（默认 1）")
    parser.add_argument("--verbose", action="store_true", help="DEBUG 级别日志")
    args = parser.parse_args()

    sys.exit(main(once=args.once, speaker_arg=args.speaker, interval=args.interval, verbose=args.verbose))

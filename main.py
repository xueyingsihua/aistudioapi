import os
import re
import json
import time
import uuid
import asyncio
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware

DATA_FILE = "config_data.json"
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com"
VERTEX_BASE_URL = "https://aiplatform.googleapis.com/v1/publishers/google"


class KeyPool:
    def __init__(self):
        self.admin_password = os.getenv("ADMIN_PASSWORD", "123456")
        self.access_token = os.getenv("PROXY_API_KEY", "")
        self.keep_alive_url = os.getenv("KEEP_ALIVE_URL", "").strip()
        self.keep_alive_interval = int(os.getenv("KEEP_ALIVE_INTERVAL", "300"))
        self.keys: List[Dict[str, Any]] = []
        self.pro_index = 0
        self.free_index = 0
        self.total_requests = 0
        self.last_ping_time = "尚未触发"
        self.last_ping_status = "等待中"
        self.load()

    def load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.keys = data.get("keys", [])
                    for item in self.keys:
                        if "tier" not in item:
                            item["tier"] = "pro"
                        if "note" not in item:
                            item["note"] = ""
                    if data.get("keep_alive_url"):
                        self.keep_alive_url = data.get("keep_alive_url")
                    if data.get("admin_password"):
                        self.admin_password = data.get("admin_password")
            except Exception:
                pass

        self._load_env_keys(os.getenv("API_KEYS", ""), default_tier="pro")
        self._load_env_keys(os.getenv("PRO_API_KEYS", ""), default_tier="pro")
        self._load_env_keys(os.getenv("FREE_API_KEYS", ""), default_tier="free")
        self.save()

    def _load_env_keys(self, raw_env: str, default_tier: str):
        if not raw_env:
            return
        for raw in raw_env.replace("\r", "\n").replace(",", "\n").split("\n"):
            line = raw.strip()
            if not line:
                continue
            tier = default_tier
            note = ""
            if "#" in line:
                parts = line.split("#", 2)
                k = parts[0].strip()
                tag = parts[1].strip().lower()
                if tag in ("pro", "free"):
                    tier = tag
                else:
                    note = parts[1].strip()
                if len(parts) > 2:
                    note = parts[2].strip()
            else:
                k = line
            if k and not any(item["key"] == k for item in self.keys):
                self.keys.append({
                    "key": k,
                    "tier": tier,
                    "note": note,
                    "enabled": True,
                    "success": 0,
                    "fail": 0,
                    "last_error": "",
                    "last_used": "-"
                })

    def save(self):
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "keys": self.keys,
                    "keep_alive_url": self.keep_alive_url,
                    "admin_password": self.admin_password
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add_keys(self, raw_text: str, tier: str = "pro", note: str = "") -> int:
        added = 0
        if tier not in ("pro", "free"):
            tier = "pro"
        for raw in raw_text.replace("\r", "\n").replace(",", "\n").split("\n"):
            line = raw.strip()
            if not line:
                continue
            item_tier = tier
            item_note = note
            if "#" in line:
                parts = line.split("#", 1)
                k = parts[0].strip()
                item_note = parts[1].strip() or note
            else:
                k = line
            if k and not any(item["key"] == k for item in self.keys):
                self.keys.append({
                    "key": k,
                    "tier": item_tier,
                    "note": item_note,
                    "enabled": True,
                    "success": 0,
                    "fail": 0,
                    "last_error": "",
                    "last_used": "-"
                })
                added += 1
        self.save()
        return added

    @staticmethod
    def is_pro_model(model_name: str) -> bool:
        m = (model_name or "").lower()
        if "flash" in m or "lite" in m or "embedding" in m or "aqa" in m:
            return False
        if "pro" in m or "ultra" in m or "exp" in m or "thinking" in m:
            return True
        return False

    def get_candidates_for_model(self, model_name: str = "") -> List[Dict[str, Any]]:
        active = [k for k in self.keys if k.get("enabled", True)]
        if not active:
            return []

        pro_keys = [k for k in active if k.get("tier", "pro") == "pro"]
        free_keys = [k for k in active if k.get("tier", "pro") == "free"]

        need_pro = self.is_pro_model(model_name)

        if need_pro:
            if pro_keys:
                start = self.pro_index % len(pro_keys)
                self.pro_index = (self.pro_index + 1) % len(pro_keys)
                return pro_keys[start:] + pro_keys[:start]
            else:
                start = self.free_index % len(free_keys)
                self.free_index = (self.free_index + 1) % len(free_keys)
                return free_keys[start:] + free_keys[:start]
        else:
            ordered_free = []
            ordered_pro = []
            if free_keys:
                start_f = self.free_index % len(free_keys)
                self.free_index = (self.free_index + 1) % len(free_keys)
                ordered_free = free_keys[start_f:] + free_keys[:start_f]
            if pro_keys:
                start_p = self.pro_index % len(pro_keys)
                if not free_keys:
                    self.pro_index = (self.pro_index + 1) % len(pro_keys)
                ordered_pro = pro_keys[start_p:] + pro_keys[:start_p]
            return ordered_free + ordered_pro


pool = KeyPool()


async def keep_alive_loop():
    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        while True:
            await asyncio.sleep(pool.keep_alive_interval)
            url = pool.keep_alive_url.strip()
            if url:
                if not url.startswith("http"):
                    url = "https://" + url
                ping_target = url.rstrip("/") + "/health"
                try:
                    resp = await client.get(ping_target)
                    pool.last_ping_time = time.strftime("%Y-%m-%d %H:%M:%S")
                    pool.last_ping_status = f"成功 (HTTP {resp.status_code})"
                except Exception as e:
                    pool.last_ping_time = time.strftime("%Y-%m-%d %H:%M:%S")
                    pool.last_ping_status = f"失败: {str(e)[:40]}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(keep_alive_loop())
    yield
    task.cancel()


app = FastAPI(title="Gemini Tavern Proxy", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_admin(x_admin_password: str = Header(default="")):
    if x_admin_password != pool.admin_password:
        raise HTTPException(status_code=401, detail="管理密码错误")


def extract_client_token(request: Request) -> str:
    # 兼容 OpenAI Bearer Token、Google x-goog-api-key 头、以及 URL ?key= 参数
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    goog_key = request.headers.get("x-goog-api-key", "")
    if goog_key:
        return goog_key.strip()
    return request.query_params.get("key", "").strip()


def check_client_auth(request: Request):
    """
    【安全修复 1】：默认强制校验密码！不再允许无密码裸奔访问。
    只要酒馆传过来的密码等于 ADMIN_PASSWORD 或 PROXY_API_KEY 即可放行。
    """
    valid_tokens = {pool.admin_password}
    if pool.access_token:
        valid_tokens.add(pool.access_token)
    token = extract_client_token(request)
    if not token or token not in valid_tokens:
        raise HTTPException(
            status_code=401,
            detail="未授权访问：请在酒馆 API Key / 反向代理密码栏填入你的反代管理密码！"
        )


@app.get("/health")
async def health():
    active = [k for k in pool.keys if k.get("enabled", True)]
    return {
        "status": "ok",
        "active_keys": len(active),
        "pro_keys": len([k for k in active if k.get("tier") == "pro"]),
        "free_keys": len([k for k in active if k.get("tier") == "free"]),
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    }


# ==================== 管理面板 API ====================
@app.post("/api/admin/login")
async def admin_login(data: Dict[str, str]):
    if data.get("password") == pool.admin_password:
        return {"ok": True}
    raise HTTPException(status_code=401, detail="密码错误")


@app.get("/api/admin/status", dependencies=[Depends(verify_admin)])
async def admin_status():
    masked_keys = []
    pro_env_list = []
    free_env_list = []
    for idx, item in enumerate(pool.keys):
        k = item["key"]
        note = item.get("note", "")
        tier = item.get("tier", "pro")
        export_item = f"{k}#{note}" if note else k
        if tier == "pro":
            pro_env_list.append(export_item)
        else:
            free_env_list.append(export_item)

        masked = k[:6] + "****" + k[-4:] if len(k) > 12 else k[:3] + "***"
        masked_keys.append({
            "index": idx,
            "masked_key": masked,
            "full_key": k,
            "tier": tier,
            "note": note,
            "enabled": item.get("enabled", True),
            "success": item.get("success", 0),
            "fail": item.get("fail", 0),
            "last_error": item.get("last_error", ""),
            "last_used": item.get("last_used", "-")
        })
    return {
        "keys": masked_keys,
        "pro_env_export": ",".join(pro_env_list),
        "free_env_export": ",".join(free_env_list),
        "total_requests": pool.total_requests,
        "keep_alive_url": pool.keep_alive_url,
        "last_ping_time": pool.last_ping_time,
        "last_ping_status": pool.last_ping_status
    }


@app.post("/api/admin/keys", dependencies=[Depends(verify_admin)])
async def admin_add_keys(data: Dict[str, str]):
    added = pool.add_keys(
        raw_text=data.get("keys", ""),
        tier=data.get("tier", "pro"),
        note=data.get("note", "")
    )
    return {"ok": True, "added": added}


@app.post("/api/admin/keys/toggle", dependencies=[Depends(verify_admin)])
async def admin_toggle_key(data: Dict[str, Any]):
    idx = data.get("index", -1)
    if 0 <= idx < len(pool.keys):
        pool.keys[idx]["enabled"] = not pool.keys[idx].get("enabled", True)
        pool.save()
    return {"ok": True}


@app.post("/api/admin/keys/tier", dependencies=[Depends(verify_admin)])
async def admin_switch_tier(data: Dict[str, Any]):
    idx = data.get("index", -1)
    if 0 <= idx < len(pool.keys):
        cur = pool.keys[idx].get("tier", "pro")
        pool.keys[idx]["tier"] = "free" if cur == "pro" else "pro"
        pool.save()
    return {"ok": True}


@app.post("/api/admin/keys/note", dependencies=[Depends(verify_admin)])
async def admin_update_note(data: Dict[str, Any]):
    idx = data.get("index", -1)
    if 0 <= idx < len(pool.keys):
        pool.keys[idx]["note"] = str(data.get("note", "")).strip()
        pool.save()
    return {"ok": True}


@app.post("/api/admin/keys/delete", dependencies=[Depends(verify_admin)])
async def admin_delete_key(data: Dict[str, Any]):
    idx = data.get("index", -1)
    if 0 <= idx < len(pool.keys):
        pool.keys.pop(idx)
        pool.save()
    return {"ok": True}


@app.post("/api/admin/keepalive", dependencies=[Depends(verify_admin)])
async def admin_set_keepalive(data: Dict[str, str]):
    pool.keep_alive_url = data.get("url", "").strip()
    pool.save()
    return {"ok": True}


@app.post("/api/admin/password", dependencies=[Depends(verify_admin)])
async def admin_change_password(data: Dict[str, str]):
    new_pwd = data.get("new_password", "").strip()
    if not new_pwd:
        raise HTTPException(status_code=400, detail="新密码不能为空")
    pool.admin_password = new_pwd
    pool.save()
    return {"ok": True}


@app.post("/api/admin/keys/test", dependencies=[Depends(verify_admin)])
async def admin_test_key(data: Dict[str, Any]):
    idx = data.get("index", -1)
    if not (0 <= idx < len(pool.keys)):
        raise HTTPException(status_code=400, detail="无效索引")
    item = pool.keys[idx]
    key = item["key"]

    async with httpx.AsyncClient(timeout=25.0) as client:
        try:
            # 先从该 Key 自己的 /v1beta/models 获取它支持的所有 generateContent 模型列表
            pro_candidates = ["gemini-2.5-pro"]
            flash_candidates = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
            r_models = await client.get(f"{GOOGLE_BASE_URL}/v1beta/models", params={"key": key})
            if r_models.status_code == 200:
                avail_pro = []
                avail_flash = []
                for m in r_models.json().get("models", []):
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" not in methods:
                        continue
                    name = m.get("name", "").replace("models/", "")
                    low = name.lower()
                    if "gemini" not in low or "tts" in low or "image" in low or "vision" in low:
                        continue
                    if "pro" in low:
                        avail_pro.append(name)
                    elif "flash" in low:
                        avail_flash.append(name)
                if avail_pro:
                    # 优先测最新 pro
                    pro_candidates = sorted(avail_pro, reverse=True)[:3]
                if avail_flash:
                    # 优先测最新 flash (如 gemini-3-flash / gemini-flash-latest / gemini-2.5-flash)
                    flash_candidates = sorted(avail_flash, reverse=True)[:4]

            test_body = {"contents": [{"parts": [{"text": "hi"}]}], "generationConfig": {"maxOutputTokens": 1}}

            # 1. 依次测试该账号支持的 Pro 模型
            last_pro_status = 0
            pro_err_detail = ""
            for pm in pro_candidates:
                r_pro = await client.post(
                    f"{GOOGLE_BASE_URL}/v1beta/models/{pm}:generateContent",
                    params={"key": key},
                    json=test_body
                )
                last_pro_status = r_pro.status_code
                if r_pro.status_code == 200:
                    item["tier"] = "pro"
                    item["enabled"] = True
                    item["last_error"] = f"✅ 实测可跑 {pm} (💎赠金/付费池)"
                    pool.save()
                    return {"ok": True, "msg": f"🎉 测活成功！该 Key 实测可调用 {pm}，已归入【💎 赠金/Pro池】"}
                try:
                    pro_err_detail = r_pro.json().get("error", {}).get("message", "")
                except Exception:
                    pro_err_detail = r_pro.text[:120]
                # 如果是 404 (该旧型号对新号停用)，继续试下一个最新型号；如果是 429/403 (无Pro权限)，直接跳去测 Flash
                if r_pro.status_code in (429, 403):
                    break

            # 2. 依次测试该账号支持的 Flash 模型
            last_flash_status = 0
            flash_err_detail = ""
            for fm in flash_candidates:
                r_flash = await client.post(
                    f"{GOOGLE_BASE_URL}/v1beta/models/{fm}:generateContent",
                    params={"key": key},
                    json=test_body
                )
                last_flash_status = r_flash.status_code
                if r_flash.status_code == 200:
                    item["tier"] = "free"
                    item["enabled"] = True
                    item["last_error"] = f"✅ 实测可跑 {fm} (⚡免费池)"
                    pool.save()
                    return {
                        "ok": True,
                        "msg": f"⚡ 测活成功！该新号实测可正常调用 {fm}（免费层级），已自动启用并归入【⚡ 免费/Flash池】！"
                    }
                try:
                    flash_err_detail = r_flash.json().get("error", {}).get("message", "")
                except Exception:
                    flash_err_detail = r_flash.text[:120]

            # 3. 如果 AI Studio 报 402 (预付款余额为0，但有GCP赠金) 或 AQ. 绑定的服务账号 Key，自动实测 Google Cloud Vertex AI 通道！
            vertex_pro_models = ["gemini-2.5-pro", "gemini-1.5-pro"]
            for vpm in vertex_pro_models:
                r_vpro = await client.post(
                    f"{VERTEX_BASE_URL}/models/{vpm}:generateContent",
                    params={"key": key},
                    json=test_body
                )
                if r_vpro.status_code == 200:
                    item["tier"] = "pro"
                    item["channel"] = "vertex"
                    item["enabled"] = True
                    item["last_error"] = f"✅ Vertex通道可跑 {vpm} (💎GCP赠金池)"
                    pool.save()
                    return {
                        "ok": True,
                        "msg": f"🎉 测活成功！已通过 Google Cloud Vertex AI 通道激活 {vpm}（绕过AI Studio 402限制，直接抵扣GCP赠金），归入【💎 赠金/Pro池】！"
                    }

            vertex_flash_models = ["gemini-2.5-flash", "gemini-2.0-flash"]
            for vfm in vertex_flash_models:
                r_vflash = await client.post(
                    f"{VERTEX_BASE_URL}/models/{vfm}:generateContent",
                    params={"key": key},
                    json=test_body
                )
                if r_vflash.status_code == 200:
                    item["tier"] = "free"
                    item["channel"] = "vertex"
                    item["enabled"] = True
                    item["last_error"] = f"✅ Vertex通道可跑 {vfm} (⚡GCP池)"
                    pool.save()
                    return {
                        "ok": True,
                        "msg": f"⚡ 测活成功！已通过 Vertex AI 通道激活 {vfm}，归入【⚡ 免费/Flash池】！"
                    }

            # 4. 所有通道与型号均无法生成内容 -> 彻底不可用
            item["enabled"] = False
            err_msg = f"❌ 不可用已暂停 (HTTP {last_flash_status}): {(flash_err_detail or pro_err_detail)[:120]}"
            item["last_error"] = err_msg
            pool.save()
            return {"ok": False, "msg": err_msg}
        except Exception as e:
            item["last_error"] = f"❌ 网络异常: {str(e)[:100]}"
            pool.save()
            return {"ok": False, "msg": str(e)}


# ==================== 【核心修复 2】：OpenAI 格式 <-> Google 原生 ?key= 双向转换器 ====================
# 解决 Google 官方 /v1beta/openai/ 端点对 AQ. 开头密钥在 Bearer 头下报 401 的问题！
# 现在无论酒馆选 Google AI Studio 原生模式还是 OpenAI 模式，底层 100% 走 ?key= 原生通道！

def openai_req_to_gemini(payload: Dict[str, Any]) -> Dict[str, Any]:
    messages = payload.get("messages", [])
    system_parts = []
    contents = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append({"text": part.get("text", "")})
                elif isinstance(part, str):
                    text_parts.append({"text": part})
        else:
            text_parts = [{"text": str(content)}]

        if not text_parts:
            continue

        if role in ("system", "developer"):
            system_parts.extend(text_parts)
        elif role == "assistant":
            contents.append({"role": "model", "parts": text_parts})
        else:
            contents.append({"role": "user", "parts": text_parts})

    if not contents:
        contents.append({"role": "user", "parts": [{"text": "Hello"}]})

    gemini_body: Dict[str, Any] = {"contents": contents}
    if system_parts:
        gemini_body["systemInstruction"] = {"parts": system_parts}

    gen_cfg: Dict[str, Any] = {}
    if "temperature" in payload and payload["temperature"] is not None:
        gen_cfg["temperature"] = payload["temperature"]
    if "top_p" in payload and payload["top_p"] is not None:
        gen_cfg["topP"] = payload["top_p"]
    if "max_tokens" in payload and payload["max_tokens"] is not None:
        gen_cfg["maxOutputTokens"] = payload["max_tokens"]
    elif "max_completion_tokens" in payload and payload["max_completion_tokens"] is not None:
        gen_cfg["maxOutputTokens"] = payload["max_completion_tokens"]
    if "stop" in payload and payload["stop"]:
        gen_cfg["stopSequences"] = payload["stop"] if isinstance(payload["stop"], list) else [payload["stop"]]
    if gen_cfg:
        gemini_body["generationConfig"] = gen_cfg

    # 默认放宽安全过滤，非常适合酒馆 RP 使用
    gemini_body["safetySettings"] = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
    ]
    return gemini_body


def gemini_resp_to_openai(gemini_data: Dict[str, Any], model_name: str) -> Dict[str, Any]:
    text = ""
    finish_reason = "stop"
    candidates = gemini_data.get("candidates", [])
    if candidates:
        cand = candidates[0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        fr = cand.get("finishReason", "")
        if fr == "MAX_TOKENS":
            finish_reason = "length"
        elif fr in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"):
            finish_reason = "content_filter"

    usage = gemini_data.get("usageMetadata", {})
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": finish_reason
        }],
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0)
        }
    }


# ==================== 1. OpenAI 兼容接口 (底层全走 ?key= 原生通道，100% 兼容 AQ. 密钥) ====================
@app.get("/v1/models")
async def openai_list_models(request: Request):
    check_client_auth(request)
    default_models = [
        "gemini-2.5-pro", "gemini-2.5-flash",
        "gemini-2.5-flash-lite", "gemini-2.0-flash",
        "gemini-1.5-pro", "gemini-1.5-flash"
    ]
    candidates = pool.get_candidates_for_model("flash")
    if not candidates:
        return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "google"} for m in default_models]}

    key_item = candidates[0]
    async with httpx.AsyncClient(timeout=20.0) as client:
        # 直接走 ?key= 的 v1beta/models，彻底避开 /v1beta/openai/ 对 AQ. 的 401 Bug
        r2 = await client.get(f"{GOOGLE_BASE_URL}/v1beta/models", params={"key": key_item["key"]})
        if r2.status_code == 200:
            models_data = r2.json().get("models", [])
            out = []
            for m in models_data:
                name = m.get("name", "").replace("models/", "")
                if name:
                    out.append({"id": name, "object": "model", "owned_by": "google"})
            return {"object": "list", "data": out}
        return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "google"} for m in default_models]}


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    check_client_auth(request)
    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    is_stream = payload.get("stream", False)
    raw_model = payload.get("model", "gemini-2.5-flash")
    model_name = raw_model.replace("models/", "")

    candidates = pool.get_candidates_for_model(model_name)
    if not candidates:
        raise HTTPException(status_code=503, detail="反代池中没有可用的 API Key，请先登录网页管理面板添加 Key！")

    pool.total_requests += 1
    candidates = candidates[:min(len(candidates), 6)]
    gemini_payload = openai_req_to_gemini(payload)
    gemini_body_bytes = json.dumps(gemini_payload, ensure_ascii=False).encode("utf-8")

    if is_stream:
        return StreamingResponse(
            openai_stream_via_native_retry(
                model_name=model_name,
                gemini_body=gemini_body_bytes,
                candidates=candidates
            ),
            media_type="text/event-stream"
        )
    else:
        return await openai_nonstream_via_native_retry(
            model_name=model_name,
            gemini_body=gemini_body_bytes,
            candidates=candidates
        )


async def openai_nonstream_via_native_retry(
    model_name: str,
    gemini_body: bytes,
    candidates: List[Dict[str, Any]]
):
    last_status = 500
    last_err_text = "All keys failed"
    async with httpx.AsyncClient(timeout=120.0) as client:
        for key_item in candidates:
            k = key_item["key"]
            key_item["last_used"] = time.strftime("%H:%M:%S")
            is_vertex = key_item.get("channel") == "vertex" or k.startswith("AQ.")
            urls_to_try = (
                [f"{VERTEX_BASE_URL}/models/{model_name}:generateContent?key={k}",
                 f"{GOOGLE_BASE_URL}/v1beta/models/{model_name}:generateContent?key={k}"]
                if is_vertex else
                [f"{GOOGLE_BASE_URL}/v1beta/models/{model_name}:generateContent?key={k}",
                 f"{VERTEX_BASE_URL}/models/{model_name}:generateContent?key={k}"]
            )
            for url in urls_to_try:
                try:
                    resp = await client.post(url, headers={"Content-Type": "application/json"}, content=gemini_body)
                    if resp.status_code == 200:
                        key_item["success"] = key_item.get("success", 0) + 1
                        key_item["last_error"] = f"OK ({model_name})"
                        if "aiplatform.googleapis.com" in url:
                            key_item["channel"] = "vertex"
                        openai_resp = gemini_resp_to_openai(resp.json(), model_name)
                        return JSONResponse(content=openai_resp, status_code=200)
                    else:
                        last_status = resp.status_code
                        last_err_text = resp.text
                        if resp.status_code in (402, 403, 401, 404):
                            continue
                except Exception as e:
                    last_err_text = str(e)
                    continue
            key_item["fail"] = key_item.get("fail", 0) + 1
            key_item["last_error"] = f"[{model_name}] HTTP {last_status}: {last_err_text[:100]}"
    return JSONResponse(status_code=last_status, content={"error": {"message": last_err_text}})


async def openai_stream_via_native_retry(
    model_name: str,
    gemini_body: bytes,
    candidates: List[Dict[str, Any]]
):
    last_err = "All keys failed in pool"
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    for key_item in candidates:
        k = key_item["key"]
        key_item["last_used"] = time.strftime("%H:%M:%S")
        is_vertex = key_item.get("channel") == "vertex" or k.startswith("AQ.")
        urls_to_try = (
            [f"{VERTEX_BASE_URL}/models/{model_name}:streamGenerateContent?alt=sse&key={k}",
             f"{GOOGLE_BASE_URL}/v1beta/models/{model_name}:streamGenerateContent?alt=sse&key={k}"]
            if is_vertex else
            [f"{GOOGLE_BASE_URL}/v1beta/models/{model_name}:streamGenerateContent?alt=sse&key={k}",
             f"{VERTEX_BASE_URL}/models/{model_name}:streamGenerateContent?alt=sse&key={k}"]
        )
        for url in urls_to_try:
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        "POST",
                        url,
                        headers={"Content-Type": "application/json"},
                        content=gemini_body
                    ) as resp:
                        if resp.status_code == 200:
                            key_item["success"] = key_item.get("success", 0) + 1
                            key_item["last_error"] = f"OK ({model_name})"
                            if "aiplatform.googleapis.com" in url:
                                key_item["channel"] = "vertex"
                            async for line in resp.aiter_lines():
                                if not line or not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if not data_str or data_str == "[DONE]":
                                    continue
                                try:
                                    g_chunk = json.loads(data_str)
                                    cands = g_chunk.get("candidates", [])
                                    if cands:
                                        parts = cands[0].get("content", {}).get("parts", [])
                                        text_piece = "".join(p.get("text", "") for p in parts if "text" in p)
                                        if text_piece:
                                            oai_chunk = {
                                                "id": chat_id,
                                                "object": "chat.completion.chunk",
                                                "created": created,
                                                "model": model_name,
                                                "choices": [{
                                                    "index": 0,
                                                    "delta": {"content": text_piece},
                                                    "finish_reason": None
                                                }]
                                            }
                                            yield f"data: {json.dumps(oai_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                                except Exception:
                                    continue
                            done_chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model_name,
                                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                            }
                            yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode("utf-8")
                            return
                        else:
                            err_body = await resp.aread()
                            err_str = err_body.decode("utf-8", "ignore")
                            last_err = err_str
                            if resp.status_code in (402, 403, 401, 404):
                                continue
            except Exception as e:
                last_err = str(e)
                continue
        key_item["fail"] = key_item.get("fail", 0) + 1
        key_item["last_error"] = f"[{model_name}] {last_err[:100]}"

    err_payload = {"error": {"message": last_err}}
    yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode("utf-8")


# ==================== 2. Google AI Studio 原生接口 (/v1beta/... & /v1/...) ====================
@app.api_route("/v1beta/{path:path}", methods=["GET", "POST"])
@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def google_native_proxy(path: str, request: Request):
    check_client_auth(request)
    m_match = re.search(r"models/([^/:]+)", path)
    model_name = m_match.group(1) if m_match else ""

    candidates = pool.get_candidates_for_model(model_name)
    if not candidates:
        raise HTTPException(status_code=503, detail="反代池中没有可用的 API Key，请先登录网页管理面板添加 Key！")

    pool.total_requests += 1
    body = await request.body()
    version_prefix = "v1beta" if request.url.path.startswith("/v1beta") else "v1"

    query_items = [(k, v) for k, v in request.query_params.multi_items() if k.lower() != "key"]
    query_str = "&".join([f"{k}={v}" for k, v in query_items])

    def build_urls_for_item(key_item: Dict[str, Any]) -> List[str]:
        api_key = key_item["key"]
        ai_url = f"{GOOGLE_BASE_URL}/{version_prefix}/{path}?key={api_key}"
        if query_str:
            ai_url += f"&{query_str}"
        if path.startswith("models/") and (":" in path):
            vx_url = f"{VERTEX_BASE_URL}/{path}?key={api_key}"
            if query_str:
                vx_url += f"&{query_str}"
            if key_item.get("channel") == "vertex":
                return [vx_url, ai_url]
            return [ai_url, vx_url]
        return [ai_url]

    is_stream = "streamGenerateContent" in path or request.query_params.get("alt") == "sse"
    candidates = candidates[:min(len(candidates), 6)]

    if is_stream:
        media_type = "text/event-stream" if request.query_params.get("alt") == "sse" else "application/json"
        return StreamingResponse(
            stream_with_retry(
                method=request.method,
                urls_builder=build_urls_for_item,
                headers_builder=lambda k: {"Content-Type": "application/json"},
                body=body,
                candidates=candidates,
                model_name=model_name
            ),
            media_type=media_type
        )
    else:
        return await request_with_retry(
            method=request.method,
            urls_builder=build_urls_for_item,
            headers_builder=lambda k: {"Content-Type": "application/json"},
            body=body,
            candidates=candidates,
            model_name=model_name
        )


async def request_with_retry(
    method: str,
    urls_builder,
    headers_builder,
    body: bytes,
    candidates: List[Dict[str, Any]],
    model_name: str = ""
):
    last_status = 500
    last_content = b'{"error": "All keys failed"}'
    async with httpx.AsyncClient(timeout=120.0) as client:
        for key_item in candidates:
            k = key_item["key"]
            key_item["last_used"] = time.strftime("%H:%M:%S")
            for target_url in urls_builder(key_item):
                try:
                    resp = await client.request(
                        method=method,
                        url=target_url,
                        headers=headers_builder(k),
                        content=body if method != "GET" else None
                    )
                    if resp.status_code == 200:
                        key_item["success"] = key_item.get("success", 0) + 1
                        key_item["last_error"] = f"OK ({model_name or 'API'})"
                        if "aiplatform.googleapis.com" in target_url:
                            key_item["channel"] = "vertex"
                        return Response(content=resp.content, status_code=200, media_type="application/json")
                    else:
                        last_status = resp.status_code
                        last_content = resp.content
                        if resp.status_code in (402, 403, 401, 404):
                            continue
                except Exception as e:
                    last_content = json.dumps({"error": str(e)}).encode("utf-8")
                    continue
            key_item["fail"] = key_item.get("fail", 0) + 1
            key_item["last_error"] = f"[{model_name}] HTTP {last_status}: {last_content.decode('utf-8', 'ignore')[:100]}"
    return Response(content=last_content, status_code=last_status, media_type="application/json")


async def stream_with_retry(
    method: str,
    urls_builder,
    headers_builder,
    body: bytes,
    candidates: List[Dict[str, Any]],
    model_name: str = ""
):
    last_err_bytes = b'data: {"error": "All keys failed in pool"}\n\n'
    for key_item in candidates:
        k = key_item["key"]
        key_item["last_used"] = time.strftime("%H:%M:%S")
        for target_url in urls_builder(key_item):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        method=method,
                        url=target_url,
                        headers=headers_builder(k),
                        content=body if method != "GET" else None
                    ) as resp:
                        if resp.status_code == 200:
                            key_item["success"] = key_item.get("success", 0) + 1
                            key_item["last_error"] = f"OK ({model_name or 'stream'})"
                            if "aiplatform.googleapis.com" in target_url:
                                key_item["channel"] = "vertex"
                            async for chunk in resp.aiter_bytes():
                                yield chunk
                            return
                        else:
                            err_body = await resp.aread()
                            last_err_bytes = err_body
                            if resp.status_code in (402, 403, 401, 404):
                                continue
            except Exception as e:
                last_err_bytes = json.dumps({"error": str(e)}).encode("utf-8")
                continue
        key_item["fail"] = key_item.get("fail", 0) + 1
        key_item["last_error"] = f"[{model_name}] {last_err_bytes.decode('utf-8', 'ignore')[:100]}"
    yield last_err_bytes


# ==================== 3. 可视化中文管理面板 ====================
INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>酒馆 Gemini 智能分池轮询反代面板</title>
<style>
  :root { --bg: #0f172a; --card: #1e293b; --text: #f8fafc; --muted: #94a3b8; --primary: #3b82f6; --success: #10b981; --danger: #ef4444; --gold: #f59e0b; }
  * { box-sizing: border-box; font-family: system-ui, -apple-system, sans-serif; }
  body { margin: 0; background: var(--bg); color: var(--text); padding: 20px; }
  .container { max-width: 1040px; margin: 0 auto; }
  .card { background: var(--card); border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid #334155; }
  h1, h2, h3 { margin-top: 0; }
  input, textarea, select { width: 100%; background: #0f172a; border: 1px solid #334155; color: var(--text); padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; }
  button { background: var(--primary); color: white; border: none; padding: 9px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; }
  button:hover { opacity: 0.9; }
  button.danger { background: var(--danger); }
  button.secondary { background: #475569; }
  button.gold { background: #d97706; }
  table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }
  th, td { text-align: left; padding: 10px; border-bottom: 1px solid #334155; }
  .badge { padding: 4px 9px; border-radius: 99px; font-size: 12px; font-weight: 600; display: inline-block; }
  .badge.ok { background: rgba(16,185,129,0.2); color: #34d399; }
  .badge.off { background: rgba(239,68,68,0.2); color: #f87171; }
  .badge.pro { background: rgba(245,158,11,0.2); color: #fbbf24; border: 1px solid rgba(245,158,11,0.4); cursor: pointer; }
  .badge.free { background: rgba(56,189,248,0.2); color: #38bdf8; border: 1px solid rgba(56,189,248,0.4); cursor: pointer; }
  .code-box { background: #090d16; padding: 10px 14px; border-radius: 8px; font-family: monospace; color: #38bdf8; word-break: break-all; margin: 6px 0; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .stat { background: #0f172a; padding: 14px; border-radius: 8px; border: 1px solid #334155; }
  .stat-val { font-size: 22px; font-weight: bold; color: #60a5fa; margin-top: 4px; }
</style>
</head>
<body>
<div class="container">
  <div id="loginView" class="card" style="max-width:420px;margin:80px auto;">
    <h2>🔐 登录反代管理面板</h2>
    <p style="color:var(--muted);font-size:14px;">默认初始密码为 <code>123456</code>（强烈建议在 Render 环境变量 <code>ADMIN_PASSWORD</code> 修改为你自己的密码）</p>
    <input type="password" id="pwdInput" placeholder="请输入管理密码..." onkeydown="if(event.key==='Enter')doLogin()">
    <button onclick="doLogin()" style="width:100%;">进入面板</button>
  </div>

  <div id="mainView" style="display:none;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px;">
      <h2>🍷 酒馆 (SillyTavern) Gemini 智能分池反代中心</h2>
      <div style="display:flex;gap:8px;">
        <button class="gold" onclick="changePassword()">🔑 修改密码</button>
        <button class="secondary" onclick="logout()">退出登录</button>
      </div>
    </div>

    <div class="grid">
      <div class="stat"><div>💎 赠金/Pro池 (可用Pro)</div><div class="stat-val" style="color:#fbbf24" id="statPro">0</div></div>
      <div class="stat"><div>⚡ 免费/Flash池 (仅Flash)</div><div class="stat-val" style="color:#38bdf8" id="statFree">0</div></div>
      <div class="stat"><div>📊 累计转发请求数</div><div class="stat-val" id="statReqs">0</div></div>
      <div class="stat"><div>💓 Render 保活状态</div><div class="stat-val" style="font-size:14px;" id="statPing">-</div></div>
    </div>

    <div class="card">
      <h3>📌 酒馆 (SillyTavern) 填写指南（两种模式均已 100% 兼容 <code>AQ.</code> 密钥 & 强制密码保护）</h3>
      <p style="margin:4px 0;color:var(--muted);font-size:14px;">1. <b>Google AI Studio 原生模式（强烈推荐，支持全部原生参数与思考链）</b>：在酒馆选 <code>Chat Completion</code> -> <code>Google AI Studio</code>，反向代理地址 (Reverse Proxy) 填：</p>
      <div class="code-box" id="googleUrl"></div>
      <p style="margin:8px 0 4px;color:var(--muted);font-size:14px;">2. <b>OpenAI 兼容模式（底层已内置 <code>?key=</code> 转换器，<code>AQ.</code> 密钥绝不报 401）</b>：在酒馆选 <code>Custom (OpenAI-compatible)</code>，API Endpoint 填：</p>
      <div class="code-box" id="openaiUrl"></div>
      <p style="margin:6px 0 0;color:#fbbf24;font-size:13px;">🔒 <b>安全锁已开启</b>：酒馆里的 <b>反向代理密码 / API Key</b> 必须填你的<b>面板管理密码</b>，不填或填错会被直接拦截，防止别人偷刷你的赠金！</p>
    </div>

    <div class="card">
      <h3>➕ 批量添加 API Key（支持 <code>AQ.</code> 开头与 <code>AIza</code> 开头）</h3>
      <div style="display:grid;grid-template-columns: 1fr 1fr;gap:12px;">
        <div>
          <label style="font-size:13px;color:var(--muted);display:block;margin-bottom:4px;">选择账号层级分组：</label>
          <select id="newTier">
            <option value="pro">💎 赠金 / 付费层级（支持 Pro + Flash 全模型）</option>
            <option value="free">⚡ 免费层级（仅支持 Flash 模型，优先消耗）</option>
          </select>
        </div>
        <div>
          <label style="font-size:13px;color:var(--muted);display:block;margin-bottom:4px;">备注标签（选填，也可在Key后加 #备注）：</label>
          <input type="text" id="newNote" placeholder="例如：大号赠金300刀 / 小号免费">
        </div>
      </div>
      <textarea id="newKeys" rows="3" placeholder="一行一个 API Key，例如：\nAQ.xxxxxxxxxxxxxxxxx#赠金号1\nAIzaSyxxxxxxxxxxxxxxx#免费小号"></textarea>
      <button onclick="addKeys()">添加到轮询池</button>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
        <h3>🔑 当前 Key 轮询池（点击层级标签可直接切换 💎Pro / ⚡Free）</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button onclick="testAllKeys()">⚡ 一键测活 & 自动分池</button>
          <button class="gold" onclick="showExportModal()">📋 一键复制 Render 环境变量 (防重启丢失)</button>
          <button class="secondary" onclick="loadStatus()">🔄 刷新</button>
        </div>
      </div>
      <div id="exportBox" style="display:none;margin-top:12px;background:#090d16;padding:14px;border-radius:8px;border:1px solid #f59e0b;">
        <div style="font-size:13px;color:#fbbf24;margin-bottom:8px;">💡 把下面两行分别复制粘贴到 Render 后台的 <b>Environment Variables</b> 中，即使 Render 容器重启或重新部署也永久不会丢号：</div>
        <div style="font-size:12px;color:var(--muted);">环境变量名 <code>PRO_API_KEYS</code> 的值（点右侧按钮复制）：</div>
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
          <input type="text" id="exportProVal" readonly style="margin:0;font-family:monospace;font-size:12px;">
          <button class="secondary" style="white-space:nowrap;padding:7px 12px;" onclick="copyText('exportProVal')">复制 PRO_API_KEYS</button>
        </div>
        <div style="font-size:12px;color:var(--muted);">环境变量名 <code>FREE_API_KEYS</code> 的值（点右侧按钮复制）：</div>
        <div style="display:flex;gap:8px;align-items:center;">
          <input type="text" id="exportFreeVal" readonly style="margin:0;font-family:monospace;font-size:12px;">
          <button class="secondary" style="white-space:nowrap;padding:7px 12px;" onclick="copyText('exportFreeVal')">复制 FREE_API_KEYS</button>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>序号</th><th>API Key</th><th>层级分组 (点击可切换)</th><th>备注</th><th>状态</th><th>成功/失败</th><th>最近状态 / 报错</th><th>操作</th></tr>
        </thead>
        <tbody id="keyTable"></tbody>
      </table>
    </div>

    <div class="card">
      <h3>💓 Render 自动保活设置（可选，点保存后生效）</h3>
      <p style="color:var(--muted);font-size:14px;">需要 24 小时保活防休眠时，点击<b>「🎯 一键提取当前网址」</b>再点<b>「保存并开启」</b>即可；如果不需要保活（节省每月免费时长），清空输入框点保存即可关闭：</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">
        <input type="text" id="keepAliveInput" placeholder="留空则不开启自动保活" style="margin:0;flex:1;min-width:240px;">
        <button class="secondary" onclick="fillCurrentUrl()" style="white-space:nowrap;">🎯 一键提取当前网址</button>
        <button onclick="saveKeepAlive()" style="white-space:nowrap;">💾 保存设置</button>
      </div>
      <p style="color:var(--muted);font-size:13px;margin-top:8px;">最近一次自检时间：<span id="pingTime">-</span></p>
    </div>
  </div>
</div>

<script>
let pwd = localStorage.getItem("admin_pwd") || "";
const origin = window.location.origin;
document.getElementById("openaiUrl").innerText = origin + "/v1";
document.getElementById("googleUrl").innerText = origin;

if (pwd) { checkAndLoad(); }

async function doLogin() {
  const val = document.getElementById("pwdInput").value.trim();
  const r = await fetch("/api/admin/login", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({password: val})
  });
  if (r.ok) {
    pwd = val;
    localStorage.setItem("admin_pwd", pwd);
    checkAndLoad();
  } else {
    alert("密码错误！");
  }
}

function logout() {
  localStorage.removeItem("admin_pwd");
  location.reload();
}

async function changePassword() {
  const newPwd = prompt("请输入新的管理密码（也是酒馆连接时填的密码）：", pwd);
  if (!newPwd || !newPwd.trim() || newPwd.trim() === pwd) return;
  const r = await fetch("/api/admin/password", {
    method: "POST",
    headers: {"Content-Type": "application/json", "x-admin-password": pwd},
    body: JSON.stringify({new_password: newPwd.trim()})
  });
  if (r.ok) {
    pwd = newPwd.trim();
    localStorage.setItem("admin_pwd", pwd);
    alert("✅ 密码修改成功！建议顺手去 Render 的 Environment 里把 ADMIN_PASSWORD 也改成新密码，防止容器重建后还原。");
  } else {
    alert("修改失败！");
  }
}

async function checkAndLoad() {
  const r = await fetch("/api/admin/status", { headers: {"x-admin-password": pwd} });
  if (!r.ok) {
    localStorage.removeItem("admin_pwd");
    document.getElementById("loginView").style.display = "block";
    document.getElementById("mainView").style.display = "none";
    return;
  }
  document.getElementById("loginView").style.display = "none";
  document.getElementById("mainView").style.display = "block";
  const data = await r.json();
  renderData(data);
}

async function loadStatus() {
  const r = await fetch("/api/admin/status", { headers: {"x-admin-password": pwd} });
  if (r.ok) renderData(await r.json());
}

function renderData(data) {
  const activePro = data.keys.filter(k => k.enabled && k.tier === "pro").length;
  const activeFree = data.keys.filter(k => k.enabled && k.tier === "free").length;
  document.getElementById("statPro").innerText = activePro + " 个启用";
  document.getElementById("statFree").innerText = activeFree + " 个启用";
  document.getElementById("statReqs").innerText = data.total_requests;
  document.getElementById("statPing").innerText = data.keep_alive_url ? data.last_ping_status : "未开启 (留空)";
  document.getElementById("pingTime").innerText = data.last_ping_time;
  document.getElementById("keepAliveInput").value = data.keep_alive_url || "";
  document.getElementById("exportProVal").value = data.pro_env_export || "";
  document.getElementById("exportFreeVal").value = data.free_env_export || "";

  const tbody = document.getElementById("keyTable");
  tbody.innerHTML = "";
  data.keys.forEach(item => {
    const isPro = item.tier === "pro";
    const tierBadge = isPro
      ? `<span class="badge pro" title="点击切换为免费/Flash池" onclick="switchTier(${item.index})">💎 赠金/Pro池</span>`
      : `<span class="badge free" title="点击切换为赠金/Pro池" onclick="switchTier(${item.index})">⚡ 免费/Flash池</span>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>#${item.index + 1}</td>
      <td><code>${item.masked_key}</code></td>
      <td>${tierBadge}</td>
      <td style="color:#94a3b8;font-size:13px;cursor:pointer;" title="点击修改备注" onclick="editNote(${item.index}, '${(item.note || '').replace(/'/g, "\\'")}')">
        <span style="border-bottom:1px dashed #64748b;padding-bottom:2px;">${item.note || '✏️ 点击加备注'}</span>
      </td>
      <td><span class="badge ${item.enabled ? 'ok' : 'off'}">${item.enabled ? '启用中' : '已暂停'}</span></td>
      <td><span style="color:#34d399">${item.success}</span> / <span style="color:#f87171">${item.fail}</span></td>
      <td style="max-width:230px;word-break:break-all;font-size:12px;color:#cbd5e1;">${item.last_error || '待命'}</td>
      <td style="display:flex;gap:6px;">
        <button class="secondary" style="padding:5px 10px;font-size:12px;" onclick="testKey(${item.index})">测活&识别</button>
        <button class="secondary" style="padding:5px 10px;font-size:12px;" onclick="toggleKey(${item.index})">${item.enabled ? '暂停' : '启用'}</button>
        <button class="danger" style="padding:5px 10px;font-size:12px;" onclick="deleteKey(${item.index})">删除</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function showExportModal() {
  const box = document.getElementById("exportBox");
  box.style.display = box.style.display === "none" ? "block" : "none";
}

function copyText(inputId) {
  const el = document.getElementById(inputId);
  el.select();
  navigator.clipboard.writeText(el.value);
  alert("已复制到剪贴板！去 Render 的 Environment Variables 粘贴即可。");
}

async function addKeys() {
  const text = document.getElementById("newKeys").value;
  const tier = document.getElementById("newTier").value;
  const note = document.getElementById("newNote").value;
  if (!text.trim()) return;
  await fetch("/api/admin/keys", {
    method: "POST", headers: {"Content-Type": "application/json", "x-admin-password": pwd},
    body: JSON.stringify({keys: text, tier, note})
  });
  document.getElementById("newKeys").value = "";
  document.getElementById("newNote").value = "";
  loadStatus();
}

async function switchTier(index) {
  await fetch("/api/admin/keys/tier", {
    method: "POST", headers: {"Content-Type": "application/json", "x-admin-password": pwd},
    body: JSON.stringify({index})
  });
  loadStatus();
}

async function editNote(index, oldNote) {
  const newNote = prompt("请输入新的备注（例如：赠金已用完 / 剩余20刀）：", oldNote || "");
  if (newNote === null) return;
  await fetch("/api/admin/keys/note", {
    method: "POST", headers: {"Content-Type": "application/json", "x-admin-password": pwd},
    body: JSON.stringify({index, note: newNote.trim()})
  });
  loadStatus();
}

async function toggleKey(index) {
  await fetch("/api/admin/keys/toggle", {
    method: "POST", headers: {"Content-Type": "application/json", "x-admin-password": pwd},
    body: JSON.stringify({index})
  });
  loadStatus();
}

async function deleteKey(index) {
  if (!confirm("确定删除这个 Key 吗？")) return;
  await fetch("/api/admin/keys/delete", {
    method: "POST", headers: {"Content-Type": "application/json", "x-admin-password": pwd},
    body: JSON.stringify({index})
  });
  loadStatus();
}

async function testKey(index) {
  const r = await fetch("/api/admin/keys/test", {
    method: "POST", headers: {"Content-Type": "application/json", "x-admin-password": pwd},
    body: JSON.stringify({index, auto_detect: true})
  });
  const res = await r.json();
  alert(res.msg);
  loadStatus();
}

async function testAllKeys() {
  const r = await fetch("/api/admin/status", { headers: {"x-admin-password": pwd} });
  const data = await r.json();
  for (let i = 0; i < data.keys.length; i++) {
    await fetch("/api/admin/keys/test", {
      method: "POST", headers: {"Content-Type": "application/json", "x-admin-password": pwd},
      body: JSON.stringify({index: i, auto_detect: true})
    });
    await loadStatus();
  }
  alert("全部 Key 测活与 Pro/Free 层级自动识别完成！记得点一下【一键复制 Render 环境变量】备份哦！");
}

function fillCurrentUrl() {
  document.getElementById("keepAliveInput").value = window.location.origin;
}

async function saveKeepAlive() {
  const url = document.getElementById("keepAliveInput").value.trim();
  await fetch("/api/admin/keepalive", {
    method: "POST", headers: {"Content-Type": "application/json", "x-admin-password": pwd},
    body: JSON.stringify({url})
  });
  alert(url ? "✅ 保活网址已保存并开启自动保活！" : "⏸️ 保活网址已清空，自动保活已关闭！");
  loadStatus();
}
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index_page():
    return INDEX_HTML

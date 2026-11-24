"""ComfyUI 연동: 내 GPU 컴퓨터에서 돌아가는 ComfyUI 서버로 3D 캐릭터 영상을 만들어요.

- 캐릭터가 말하는 장면: Wan 2.2 S2V (이미지 + 목소리 → 입 모양·몸짓이 맞는 영상)
- 장면 영상(캐릭터 모드 끔): Wan 2.2 TI2V 5B (글 → 영상)

ComfyUI의 API 형식(prompt JSON)을 코드에서 직접 만들어 /prompt 로 보내고,
/history 로 끝났는지 확인한 뒤 /view 로 결과 mp4를 받아와요.
"""
import json
import math
import os
import time
import uuid

import requests

# ComfyUI models 폴더 안에 있어야 하는 파일 (설정 화면에서 이름을 바꿀 수 있어요)
DEFAULT_COMFY = {
    "url": "http://127.0.0.1:8188",
    # 말하는 캐릭터 (Wan 2.2 S2V 14B)
    "s2v_unet": "wan2.2_s2v_14B_fp8_scaled.safetensors",
    "s2v_lora": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",  # 비우면 LoRA 없이 20스텝
    "audio_encoder": "wav2vec2_large_english_fp16.safetensors",
    "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "s2v_vae": "wan_2.1_vae.safetensors",
    "s2v_width": 480,
    "s2v_height": 832,
    # 장면 영상 (Wan 2.2 TI2V 5B)
    "t2v_unet": "wan2.2_ti2v_5B_fp16.safetensors",
    "t2v_vae": "wan2.2_vae.safetensors",
    "t2v_width": 704,
    "t2v_height": 1280,
    "t2v_seconds": 5,
    "timeout_min": 40,          # 한 장면이 이 시간 안에 안 끝나면 실패로 보고 다음 방법으로 넘어가요
}

S2V_FPS = 16
S2V_CHUNK = 77                  # Wan S2V가 한 번에 만드는 프레임 수 (16fps 기준 약 4.8초)
T2V_FPS = 24
STYLE_3D = ("high quality 3D animated film style, Pixar-like stylized character, soft cinematic lighting, "
            "smooth motion, consistent character design")
NEGATIVE = ("text, letters, subtitles, watermark, logo, blurry, low quality, deformed face, extra fingers, "
            "bad hands, jitter, flicker, static image, horror, blood, ghost")


def settings(cfg: dict) -> dict:
    out = dict(DEFAULT_COMFY)
    for k, v in (cfg.get("comfyui") or {}).items():
        if k in out and v is not None and (v != "" or k == "s2v_lora"):   # LoRA는 비우면 안 씀
            out[k] = v
    out["url"] = str(out["url"]).rstrip("/")
    return out


# ───────────────────────── 서버와 주고받기 ─────────────────────────
class ComfyClient:
    def __init__(self, url: str, timeout_min: float = 40):
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex
        self.timeout = float(timeout_min) * 60

    def get(self, path, **kw):
        r = requests.get(self.url + path, timeout=kw.pop("timeout", 30), **kw)
        r.raise_for_status()
        return r

    def upload(self, path: str, name: str | None = None) -> str:
        """input 폴더에 파일을 올리고 ComfyUI 안에서 쓸 이름을 돌려줘요 (이미지·오디오 모두)."""
        name = name or os.path.basename(path)
        with open(path, "rb") as f:
            r = requests.post(self.url + "/upload/image", timeout=120,
                              files={"image": (name, f)},
                              data={"type": "input", "overwrite": "true", "subfolder": "shindang"})
        r.raise_for_status()
        j = r.json()
        return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]

    def queue(self, graph: dict) -> str:
        r = requests.post(self.url + "/prompt", timeout=60,
                          json={"prompt": graph, "client_id": self.client_id})
        if r.status_code == 400:
            raise RuntimeError("ComfyUI가 작업을 거절했어요: " + _explain(r.json()))
        r.raise_for_status()
        return r.json()["prompt_id"]

    def wait(self, prompt_id: str) -> dict:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            h = self.get(f"/history/{prompt_id}").json().get(prompt_id)
            if h:
                st = h.get("status", {})
                if st.get("status_str") == "error":
                    msg = next((m[1].get("exception_message", "") for m in st.get("messages", [])
                                if m[0] == "execution_error"), "")
                    raise RuntimeError(f"ComfyUI 실행 오류: {msg[:300]}")
                if st.get("completed") or h.get("outputs"):
                    return h["outputs"]
            time.sleep(4)
        raise TimeoutError("ComfyUI 작업이 제한 시간 안에 끝나지 않았어요.")

    def download_video(self, outputs: dict, out_path: str) -> str:
        for node_out in outputs.values():
            for key in ("images", "videos", "gifs"):
                for f in node_out.get(key, []):
                    if str(f.get("filename", "")).lower().endswith((".mp4", ".webm", ".mov", ".mkv")):
                        r = self.get("/view", params={"filename": f["filename"], "subfolder": f.get("subfolder", ""),
                                                      "type": f.get("type", "output")}, timeout=300)
                        with open(out_path, "wb") as fh:
                            fh.write(r.content)
                        return out_path
        raise RuntimeError("ComfyUI 결과에 영상 파일이 없어요.")

    def run(self, graph: dict, out_path: str) -> str:
        return self.download_video(self.wait(self.queue(graph)), out_path)


def _explain(err: dict) -> str:
    parts = [str((err.get("error") or {}).get("message", ""))]
    for nid, ne in (err.get("node_errors") or {}).items():
        for e in ne.get("errors", []):
            parts.append(f"[{ne.get('class_type', nid)}] {e.get('message', '')} {e.get('details', '')}".strip())
    return " / ".join(p for p in parts if p)[:500]


# ───────────────────────── 워크플로 (API 형식) ─────────────────────────
class Graph:
    def __init__(self):
        self.nodes, self.n = {}, 0

    def add(self, class_type: str, **inputs):
        self.n += 1
        nid = str(self.n)
        self.nodes[nid] = {"class_type": class_type, "inputs": inputs}
        return nid

    @staticmethod
    def out(nid, idx=0):
        return [nid, idx]


def _save_video(g: Graph, images, fps, prefix):
    video = g.add("CreateVideo", images=g.out(images), fps=float(fps))
    g.add("SaveVideo", video=g.out(video), filename_prefix=prefix, format="mp4", codec="h264")


def s2v_frames(seconds: float) -> tuple[int, int]:
    """목소리 길이 → (청크 수, 전체 프레임 수). 한 청크는 77프레임."""
    need = max(1, math.ceil(seconds * S2V_FPS))
    chunks = max(1, math.ceil(need / S2V_CHUNK))
    return chunks, chunks * S2V_CHUNK


def build_s2v(c: dict, image: str, audio: str, prompt: str, seconds: float, seed: int,
              prefix: str = "shindang/talk") -> dict:
    """Wan 2.2 S2V: 기준 이미지 + 목소리 → 말하는 3D 캐릭터 영상 (긴 대사는 이어 붙이기)."""
    g = Graph()
    unet = g.add("UNETLoader", unet_name=c["s2v_unet"], weight_dtype="default")
    model = unet
    lora = c.get("s2v_lora")
    if lora:
        model = g.add("LoraLoaderModelOnly", model=g.out(unet), lora_name=lora, strength_model=1.0)
    model = g.add("ModelSamplingSD3", model=g.out(model), shift=8.0)
    clip = g.add("CLIPLoader", clip_name=c["text_encoder"], type="wan", device="default")
    vae = g.add("VAELoader", vae_name=c["s2v_vae"])
    aenc = g.add("AudioEncoderLoader", audio_encoder_name=c["audio_encoder"])
    aud = g.add("LoadAudio", audio=audio)
    aout = g.add("AudioEncoderEncode", audio_encoder=g.out(aenc), audio=g.out(aud))
    img = g.add("LoadImage", image=image)
    pos = g.add("CLIPTextEncode", clip=g.out(clip), text=prompt)
    neg = g.add("CLIPTextEncode", clip=g.out(clip), text=NEGATIVE)
    steps, cfg_scale = (4, 1.0) if lora else (20, 6.0)
    sampler = dict(seed=seed, steps=steps, cfg=cfg_scale, sampler_name="uni_pc", scheduler="simple", denoise=1.0)

    chunks, _ = s2v_frames(seconds)
    cond = g.add("WanSoundImageToVideo", positive=g.out(pos), negative=g.out(neg), vae=g.out(vae),
                 width=int(c["s2v_width"]), height=int(c["s2v_height"]), length=S2V_CHUNK, batch_size=1,
                 audio_encoder_output=g.out(aout), ref_image=g.out(img))
    lat = g.add("KSampler", model=g.out(model), positive=g.out(cond, 0), negative=g.out(cond, 1),
                latent_image=g.out(cond, 2), **sampler)
    total = lat
    for _ in range(chunks - 1):
        ext = g.add("WanSoundImageToVideoExtend", positive=g.out(pos), negative=g.out(neg), vae=g.out(vae),
                    length=S2V_CHUNK, video_latent=g.out(total), audio_encoder_output=g.out(aout),
                    ref_image=g.out(img))
        nxt = g.add("KSampler", model=g.out(model), positive=g.out(ext, 0), negative=g.out(ext, 1),
                    latent_image=g.out(ext, 2), **sampler)
        total = g.add("LatentConcat", samples1=g.out(total), samples2=g.out(nxt), dim="t")
    # 공식 템플릿과 같은 처리: 첫 프레임이 VAE에서 과하게 구워지는 문제 → 첫 latent를 하나 더 붙여 디코딩한 뒤 앞 3프레임 버림
    first = g.add("LatentCut", samples=g.out(total), dim="t", index=0, amount=1)
    total = g.add("LatentConcat", samples1=g.out(first), samples2=g.out(total), dim="t")
    decoded = g.add("VAEDecode", samples=g.out(total), vae=g.out(vae))
    images = g.add("ImageFromBatch", image=g.out(decoded), batch_index=3, length=4096)
    _save_video(g, images, S2V_FPS, prefix)
    return g.nodes


def build_t2v(c: dict, prompt: str, seed: int, prefix: str = "shindang/scene") -> dict:
    """Wan 2.2 TI2V 5B: 장면 설명 → 세로 3D 장면 영상."""
    g = Graph()
    unet = g.add("UNETLoader", unet_name=c["t2v_unet"], weight_dtype="default")
    model = g.add("ModelSamplingSD3", model=g.out(unet), shift=8.0)
    clip = g.add("CLIPLoader", clip_name=c["text_encoder"], type="wan", device="default")
    vae = g.add("VAELoader", vae_name=c["t2v_vae"])
    pos = g.add("CLIPTextEncode", clip=g.out(clip), text=prompt)
    neg = g.add("CLIPTextEncode", clip=g.out(clip), text=NEGATIVE)
    length = int(float(c["t2v_seconds"]) * T2V_FPS) // 4 * 4 + 1
    lat = g.add("Wan22ImageToVideoLatent", vae=g.out(vae), width=int(c["t2v_width"]),
                height=int(c["t2v_height"]), length=length, batch_size=1)
    samp = g.add("KSampler", model=g.out(model), positive=g.out(pos), negative=g.out(neg),
                 latent_image=g.out(lat), seed=seed, steps=20, cfg=5.0, sampler_name="uni_pc",
                 scheduler="simple", denoise=1.0)
    images = g.add("VAEDecode", samples=g.out(samp), vae=g.out(vae))
    _save_video(g, images, T2V_FPS, prefix)
    return g.nodes


# ───────────────────────── 연결 확인 ─────────────────────────
NEEDED = [("diffusion_models", "s2v_unet"), ("diffusion_models", "t2v_unet"), ("loras", "s2v_lora"),
          ("audio_encoders", "audio_encoder"), ("text_encoders", "text_encoder"),
          ("vae", "s2v_vae"), ("vae", "t2v_vae")]


def check(cfg: dict) -> dict:
    """서버가 켜져 있는지, 필요한 노드·모델 파일이 있는지 확인 (영상은 만들지 않음)."""
    c = settings(cfg)
    cl = ComfyClient(c["url"])
    try:
        stats = cl.get("/system_stats", timeout=10).json()
    except requests.ConnectionError:
        return {"ok": False, "detail": f"{c['url']} 에 연결하지 못했어요. ComfyUI가 켜져 있는지, 주소가 맞는지 확인해주세요."}
    dev = (stats.get("devices") or [{}])[0]
    gpu = dev.get("name", "?")
    vram = round((dev.get("vram_total") or 0) / 1024 ** 3)
    info = cl.get("/object_info/WanSoundImageToVideo", timeout=20).json()
    if "WanSoundImageToVideo" not in info:
        return {"ok": False, "detail": "ComfyUI가 너무 오래된 버전이에요. 업데이트해주세요."}
    missing = []
    for folder, key in NEEDED:
        name = c.get(key)
        if not name:
            continue
        try:
            files = cl.get(f"/models/{folder}", timeout=20).json()
        except requests.HTTPError:
            files = []
        if name not in files:
            missing.append(name)
    if missing:
        return {"ok": False, "detail": f"{gpu} 연결됨 · 모델 파일이 없어요: " + ", ".join(missing)}
    note = " · VRAM이 16GB보다 작아서 느릴 수 있어요" if 0 < vram < 16 else ""
    return {"ok": True, "detail": f"ComfyUI 연결됨 ({gpu}, {vram}GB){note}"}


if __name__ == "__main__":
    # 워크플로 예시 JSON 뽑기: python comfy.py  → workflows/ 폴더에 저장
    here = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(here, "workflows")
    os.makedirs(d, exist_ok=True)
    c = dict(DEFAULT_COMFY)
    with open(os.path.join(d, "s2v_talking_character_api.json"), "w", encoding="utf-8") as f:
        json.dump(build_s2v(c, "shindang/dongja.png", "shindang/line1.mp3",
                            "A cute 3D animated Korean shrine child spirit talking to the camera. " + STYLE_3D,
                            6.0, 42), f, ensure_ascii=False, indent=2)
    with open(os.path.join(d, "t2v_scene_api.json"), "w", encoding="utf-8") as f:
        json.dump(build_t2v(c, "A cozy Korean fortune-telling shrine at night, candles flickering. " + STYLE_3D, 42),
                  f, ensure_ascii=False, indent=2)
    print("saved to", d)

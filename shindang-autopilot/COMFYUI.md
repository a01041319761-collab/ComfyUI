# ComfyUI로 3D 캐릭터 영상 만들기

앱이 매일 만드는 영상의 "캐릭터가 말하는 장면"을 **내 GPU 컴퓨터의 ComfyUI**에서 만들어요.
캐릭터 이미지(3D 애니메이션풍)와 ElevenLabs 목소리를 넣으면, Wan 2.2 S2V 모델이 입 모양과 몸짓을 맞춘 3D 영상을 만들어요.
캐릭터 모드를 끄면 장면 영상도 Veo 대신 Wan 2.2(5B)로 만들어요.

```
대본(Claude) → 목소리(ElevenLabs) → [ComfyUI: 캐릭터 이미지 + 목소리 → 3D 말하는 영상] → 자막·엔딩(FFmpeg) → 업로드
```

ComfyUI가 꺼져 있거나 실패하면 자동으로 다음 방법을 써요: **fal.ai(키가 있을 때) → 캐릭터 이미지 확대 화면**.
그래서 ComfyUI 컴퓨터가 꺼져 있는 날에도 영상은 올라가요.

## 1. 필요한 컴퓨터

| 항목 | 권장 | 최소 |
| --- | --- | --- |
| 그래픽카드 | NVIDIA RTX 4090 / 5090 (VRAM 24GB 이상) | RTX 3060 12GB (많이 느림) |
| 메모리(RAM) | 64GB | 32GB |
| 저장공간 | 모델 약 40GB + 여유 | |

- 말하는 장면 하나(약 5초)가 4090 + 가속 LoRA 기준 대략 1~3분, 하루 영상(5~7장면)이면 10~20분 정도 걸려요. GPU에 따라 많이 달라요.
- GPU 컴퓨터가 없으면 RunPod·Vast.ai 같은 GPU 클라우드에 ComfyUI를 띄우고 그 주소를 넣어도 돼요.

## 2. ComfyUI 설치

이 저장소(포크)가 ComfyUI 자체예요. 둘 중 편한 방법으로 설치하세요.

**A. 윈도우 간편 설치 (추천)**: https://www.comfy.org/download 에서 ComfyUI Desktop 또는 Portable을 받아 실행.

**B. 이 저장소로 직접 설치**
```
git clone https://github.com/a01041319761-collab/comfyui
cd comfyui
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
pip install -r requirements.txt
python main.py --listen 127.0.0.1 --port 8188
```
(CUDA 버전에 맞는 PyTorch 주소는 https://pytorch.org 에서 확인하세요.)

브라우저에서 http://127.0.0.1:8188 이 열리면 성공이에요.

## 3. 모델 받기

아래 파일을 받아서 ComfyUI의 `models/` 아래 각 폴더에 넣어주세요. (폴더가 없으면 만드세요.)

**말하는 캐릭터 (필수)**

| 파일 | 넣을 폴더 | 크기 |
| --- | --- | --- |
| [wan2.2_s2v_14B_fp8_scaled.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors) | `models/diffusion_models/` | 15.3GB |
| [wav2vec2_large_english_fp16.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/audio_encoders/wav2vec2_large_english_fp16.safetensors) | `models/audio_encoders/` | 0.6GB |
| [umt5_xxl_fp8_e4m3fn_scaled.safetensors](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors) | `models/text_encoders/` | 6.3GB |
| [wan_2.1_vae.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors) | `models/vae/` | 0.2GB |
| [wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors) | `models/loras/` | 가속용 (4스텝) |

**장면 영상 (캐릭터 모드를 끌 때만)**

| 파일 | 넣을 폴더 |
| --- | --- |
| [wan2.2_ti2v_5B_fp16.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors) | `models/diffusion_models/` |
| [wan2.2_vae.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors) | `models/vae/` |

> 파일 이름은 그대로 두세요. 앱이 이 이름으로 찾아요. 이름을 바꿨다면 `data/config.json`의 `"comfyui"` 항목에 적어주면 돼요 (`comfy.py`의 `DEFAULT_COMFY` 참고).

## 4. 앱과 연결

1. ComfyUI를 켜둡니다.
2. 신당 오토파일럿 → **설정 → 진행 캐릭터 → 3D 영상 만드는 곳**에서 **내 컴퓨터 ComfyUI (3D)** 선택.
3. **ComfyUI 주소**에 `http://127.0.0.1:8188` (같은 컴퓨터) 또는 `http://GPU컴퓨터IP:8188` (다른 컴퓨터) 입력 → **연결 확인**.
   - "ComfyUI 연결됨 (그래픽카드 이름)"이 나오면 끝. 빠진 모델 파일이 있으면 이름을 알려줘요.
   - 다른 컴퓨터에서 쓸 때는 ComfyUI를 `--listen 0.0.0.0` 으로 켜고, 같은 집 와이파이 안에서만 쓰세요 (ComfyUI에는 비밀번호가 없어요).
4. 설정 저장 → 오늘 화면에서 **만들기**로 한 번 시험해 보세요.

## 5. 3D로 잘 나오게 하는 팁

- **캐릭터 이미지가 3D여야 영상도 3D예요.** 설정에서 캐릭터마다 "AI로 이미지 만들기"를 누르면 Pixar풍 3D 프롬프트로 만들어요. 정면을 보는 상반신 세로 이미지가 입 모양이 가장 잘 맞아요.
- 캐릭터마다 적어둔 **움직임 설명(영어)** 이 영상 프롬프트에 들어가요. 예: `waving a small round fan, cheerful hand gestures`.
- 화질이 아쉬우면 `data/config.json`에서 `"comfyui": {"s2v_lora": ""}` 로 가속 LoRA를 끄면 20스텝으로 더 정성껏 만들어요 (약 5배 느림).
- 해상도는 기본 480×832(세로)로 만들고 앱이 1080×1920으로 키워요. VRAM이 넉넉하면 `s2v_width: 576, s2v_height: 1024` 처럼 올려보세요 (16의 배수).

## 6. 워크플로 직접 열어보기

`workflows/` 폴더에 앱이 ComfyUI로 보내는 작업(API 형식)의 예시가 있어요.
- `s2v_talking_character_api.json` — 말하는 캐릭터 (6초 대사 = 77프레임 × 2구간)
- `t2v_scene_api.json` — 장면 영상

대사가 길면 앱이 77프레임(약 4.8초)씩 이어 붙이는 구간을 자동으로 늘려요. 예시를 다시 만들려면 `python comfy.py`.
ComfyUI 화면에서 직접 만져보고 싶으면 ComfyUI 메뉴의 **템플릿 → Wan2.2 S2V** 를 열면 같은 구조예요.

## 7. 문제 해결

| 증상 | 해결 |
| --- | --- |
| 연결 확인에서 "연결하지 못했어요" | ComfyUI가 켜져 있는지, 주소·포트가 맞는지 확인 |
| "모델 파일이 없어요: …" | 3번 표의 파일을 해당 폴더에 넣고 ComfyUI 재시작 |
| "너무 오래된 버전" | ComfyUI 업데이트 (`git pull` 후 `pip install -r requirements.txt`) |
| 메모리 부족(OOM) | 해상도를 낮추거나, ComfyUI를 `--lowvram` 으로 실행 |
| 기록에 "립싱크 3/6" 처럼 나옴 | 일부 장면이 실패해서 fal.ai나 정지 화면으로 대신했다는 뜻. 실행 기록(로그)에 이유가 남아요 |

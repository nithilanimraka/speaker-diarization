# main.py (Fully Asynchronous)
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState
from google.cloud.speech import SpeechAsyncClient
from google.cloud import speech
import logging
import os

try:
    import webrtcvad 
except Exception:  # pragma: no cover
    webrtcvad = None

logging.basicConfig(level=logging.INFO)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Use the SpeechAsyncClient
client = SpeechAsyncClient()

diarization_config = speech.SpeakerDiarizationConfig(
    enable_speaker_diarization=True,
    min_speaker_count=2,
    max_speaker_count=2,
)

config = speech.RecognitionConfig(
    encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
    sample_rate_hertz=16000,
    language_code="en-US",
    diarization_config=diarization_config,
    enable_automatic_punctuation=True,
    use_enhanced=True,
    model="phone_call",
    enable_word_time_offsets=True,
)

streaming_config = speech.StreamingRecognitionConfig(
    config=config,
    interim_results=True
)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    async def request_generator():
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)

        # Audio/VAD parameters
        sample_rate_hz = 16000
        frame_ms = 30  # Allowed by WebRTC VAD: 10, 20, 30ms
        bytes_per_sample = 2  # 16-bit PCM
        frame_bytes = int(sample_rate_hz * frame_ms / 1000) * bytes_per_sample

        use_vad = bool(int(os.getenv("VAD_ENABLED", "1")))
        vad_aggressiveness = int(os.getenv("VAD_AGGRESSIVENESS", "3"))  # 0-3
        preroll_ms = int(os.getenv("VAD_PREROLL_MS", "150"))
        hangover_ms = int(os.getenv("VAD_HANGOVER_MS", "400"))

        if use_vad and webrtcvad is None:
            logging.warning("webrtcvad not available; disabling VAD gating.")
            use_vad = False

        if not use_vad:
            try:
                while True:
                    data = await websocket.receive_bytes()
                    yield speech.StreamingRecognizeRequest(audio_content=data)
            except WebSocketDisconnect:
                logging.info("Client disconnected from websocket.")
                return

        # VAD setup
        vad = webrtcvad.Vad(vad_aggressiveness) if use_vad else None
        buffer = bytearray()
        speech_active = False
        from collections import deque
        preroll_frames = max(1, preroll_ms // frame_ms)
        hangover_frames = max(1, hangover_ms // frame_ms)
        preroll = deque(maxlen=preroll_frames)
        unvoiced_count = 0

        try:
            while True:
                chunk = await websocket.receive_bytes()
                buffer.extend(chunk)

                while len(buffer) >= frame_bytes:
                    frame = bytes(buffer[:frame_bytes])
                    del buffer[:frame_bytes]

                    is_speech = vad.is_speech(frame, sample_rate_hz)

                    if not speech_active:
                        preroll.append(frame)
                        if is_speech:
                            speech_active = True
                            unvoiced_count = 0
                            # Flush preroll first, then current frame
                            for f in list(preroll):
                                yield speech.StreamingRecognizeRequest(audio_content=f)
                            preroll.clear()
                            yield speech.StreamingRecognizeRequest(audio_content=frame)
                    else:
                        # In active speech: always forward frames
                        yield speech.StreamingRecognizeRequest(audio_content=frame)

                        if is_speech:
                            unvoiced_count = 0
                        else:
                            unvoiced_count += 1
                            if unvoiced_count >= hangover_frames:
                                speech_active = False
                                unvoiced_count = 0
                                preroll.clear()
        except WebSocketDisconnect:
            logging.info("Client disconnected from websocket.")
            return

    try:
        requests = request_generator()
        responses = await client.streaming_recognize(requests=requests)

        prev_speaker_tag = None
        async for response in responses:
            for result in response.results:
                if result.is_final:
                    if not result.alternatives or not result.alternatives[0].words:
                        continue

                    words = result.alternatives[0].words
                    transcript = result.alternatives[0].transcript

                    # Majority vote over word-level speaker_tag within this final segment
                    tag_counts = {}
                    for w in words:
                        tag = getattr(w, 'speaker_tag', None)
                        if tag is None:
                            continue
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1

                    if not tag_counts:
                        continue

                    candidate_tag = max(tag_counts.items(), key=lambda kv: kv[1])[0]

                    # Hysteresis smoothing: avoid switching on very short runs
                    # Count how many trailing words belong to the candidate tag
                    tail_run = 0
                    for w in reversed(words):
                        if getattr(w, 'speaker_tag', None) == candidate_tag:
                            tail_run += 1
                        else:
                            break

                    # If switch is attempted but tail run is short, keep previous speaker
                    min_switch_words = 3
                    if prev_speaker_tag is not None and candidate_tag != prev_speaker_tag and tail_run < min_switch_words:
                        speaker_tag = prev_speaker_tag
                    else:
                        speaker_tag = candidate_tag
                        prev_speaker_tag = speaker_tag

                    logging.info(f"Sending final transcript: Tag {speaker_tag} - {transcript}")

                    # --- THIS IS THE ONLY CHANGE ---
                    # Send the raw speaker_tag, not a pre-determined label
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.send_json({
                            "speaker_tag": speaker_tag, # Changed from "speaker"
                            "transcript": transcript,
                            "is_final": True
                        })
                    else:
                        logging.warning("WebSocket is closed; unable to send final transcript.")

    except Exception as e:
        if not isinstance(e, WebSocketDisconnect):
                logging.error(f"An error occurred: {e}", exc_info=True)
    finally:
        logging.info("Connection processing finished.")
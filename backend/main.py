# main.py (Fully Asynchronous)
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState
from google.cloud.speech import SpeechAsyncClient
from google.cloud import speech
import logging
import os
import time

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
    model="video",
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
        vad_aggressiveness = int(os.getenv("VAD_AGGRESSIVENESS", "2"))  # 0-3
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
            # Use receive timeout to inject keepalive during long silence
            recv_timeout_sec = max(0.02, frame_ms / 1000)
            zero_frame = b"\x00" * frame_bytes
            keepalive_ms = int(os.getenv("SILENCE_KEEPALIVE_MS", "1000"))
            last_client_data_ts = time.monotonic()
            while True:
                try:
                    chunk = await asyncio.wait_for(websocket.receive_bytes(), timeout=recv_timeout_sec)
                    buffer.extend(chunk)
                    last_client_data_ts = time.monotonic()
                except asyncio.TimeoutError:
                    # No data from client; if not in speech and exceeded keepalive interval, send a zero frame
                    if not speech_active and (time.monotonic() - last_client_data_ts) * 1000 >= keepalive_ms:
                        yield speech.StreamingRecognizeRequest(audio_content=zero_frame)
                    continue

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

        # Using per-result majority vote only (no cross-result confirmation)
        # Track last max word end time to avoid emitting duplicate finals
        last_max_word_end_ns = None
        async for response in responses:
            for result in response.results:
                if result.is_final:
                    if not result.alternatives or not result.alternatives[0].words:
                        continue

                    words = result.alternatives[0].words
                    transcript = result.alternatives[0].transcript

                    # Skip empty/whitespace transcripts
                    if not transcript or not transcript.strip():
                        continue

                    # Skip duplicates: if max end time hasn't advanced, this is likely a repeated final
                    try:
                        def to_ns(d):
                            # d is a Duration with seconds and nanos
                            return int(getattr(d, 'seconds', 0)) * 1_000_000_000 + int(getattr(d, 'nanos', 0))

                        current_max_end_ns = max(
                            [to_ns(getattr(w, 'end_time', None)) for w in words if getattr(w, 'end_time', None) is not None] or [None]
                        )
                        if current_max_end_ns is not None and last_max_word_end_ns is not None and current_max_end_ns <= last_max_word_end_ns:
                            continue
                        if current_max_end_ns is not None:
                            last_max_word_end_ns = current_max_end_ns
                    except Exception:
                        pass

                    # Prepare per-word tag sequence for readable logging later
                    tags_sequence = [getattr(w, 'speaker_tag', None) for w in words]

                    # Majority vote over word-level speaker_tag within this final segment
                    # with extra weight on trailing words to stabilize speaker at segment end
                    tag_counts = {}
                    for w in words:
                        tag = getattr(w, 'speaker_tag', None)
                        if tag is None:
                            continue
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1

                    pre_weight_counts = dict(tag_counts)

                    # Tail weighting: last N words get additional weight (e.g., count double)
                    tail_words = int(os.getenv("VOTE_TAIL_WORDS", "5"))
                    tail_weight = int(os.getenv("VOTE_TAIL_WEIGHT", "2"))
                    if tail_weight > 1 and tail_words > 0:
                        start_idx = max(0, len(words) - tail_words)
                        for w in words[start_idx:]:
                            tag = getattr(w, 'speaker_tag', None)
                            if tag is None:
                                continue
                            # add (tail_weight - 1) so total equals base 1 + extra
                            tag_counts[tag] = tag_counts.get(tag, 0) + (tail_weight - 1)

                    # We'll log a readable block after choosing the speaker_tag

                    if not tag_counts:
                        continue

                    # Per-result majority winner
                    speaker_tag = max(tag_counts.items(), key=lambda kv: kv[1])[0]

                    # Pretty segment log: transcript, per-word tags spaced, counts (pre/weighted), and chosen tag
                    try:
                        # chunk tags into readable rows
                        chunk_size = 32
                        tag_rows = [
                            " ".join(str(t) for t in tags_sequence[i:i+chunk_size])
                            for i in range(0, len(tags_sequence), chunk_size)
                        ]
                        logging.info(
                            "\n==== Diarization Segment ====\n"
                            "Transcript: %s\n"
                            "Tags (by word):\n  %s\n"
                            "Counts (pre-weight): %s\n"
                            "Counts (weighted, last %d x%d): %s\n"
                            "Chosen speaker_tag: %s\n"
                            "============================\n",
                            transcript,
                            "\n  ".join(tag_rows) if tag_rows else "",
                            pre_weight_counts,
                            tail_words,
                            tail_weight,
                            tag_counts,
                            speaker_tag,
                        )
                    except Exception:
                        pass

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

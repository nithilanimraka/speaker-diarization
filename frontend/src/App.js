import React, { useState, useRef, useEffect } from 'react';
import './App.css';

function App() {
  // ... (keep useState, useRef, useEffect hooks as they are) ...
  const [isRecording, setIsRecording] = useState(false);
  const [transcript, setTranscript] = useState([]);
  const [speakerMap, setSpeakerMap] = useState({});
  const firstSpeakerTag = useRef(null);
  const ws = useRef(null);
  const audioContext = useRef(null);
  const processor = useRef(null);
  const stream = useRef(null);


  const getSpeakerLabel = (tag) => {
    return speakerMap[tag] || `Speaker ${tag}`;
  };

  const startRecording = async () => {
    if (isRecording) return;

    setTranscript([]);
    setSpeakerMap({});
    // We no longer need the firstSpeakerTag.current ref
    
    try {
      ws.current = new WebSocket('ws://localhost:8000/ws');
      ws.current.onopen = () => setIsRecording(true);

      ws.current.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.is_final) {
          const tag = data.speaker_tag;

          // --- FINAL, MORE ROBUST LOGIC ---
          setSpeakerMap(prevMap => {
            // If this speaker tag is already known, do nothing.
            if (prevMap[tag]) {
              return prevMap;
            }
            
            // If the map of speakers is empty, this is the first speaker.
            // Assign them as "User".
            if (Object.keys(prevMap).length === 0) {
              return { ...prevMap, [tag]: 'User' };
            }

            // If the map has one speaker, this must be the second.
            // Assign them as "AI Agent".
            if (Object.keys(prevMap).length === 1) {
              return { ...prevMap, [tag]: 'AI Agent' };
            }

            // If more than 2 speakers are somehow detected, do nothing.
            return prevMap;
          });

          setTranscript(prev => [...prev, { speaker_tag: tag, text: data.transcript }]);
        }
      };
      
      ws.current.onclose = () => console.log("WebSocket connection closed");
      ws.current.onerror = (error) => console.error("WebSocket error:", error);

      // ... (the rest of the function for getting audio media is the same) ...
      stream.current = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
          sampleRate: 16000
        }
      });
      audioContext.current = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      const source = audioContext.current.createMediaStreamSource(stream.current);
      processor.current = audioContext.current.createScriptProcessor(4096, 1, 1);
      processor.current.onaudioprocess = (e) => {
        const inputData = e.inputBuffer.getChannelData(0);
        const pcmData = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
            let s = inputData[i];
            if (s > 1) s = 1;
            if (s < -1) s = -1;
            pcmData[i] = s * 32767;
        }
        if (ws.current && ws.current.readyState === WebSocket.OPEN) {
            ws.current.send(pcmData.buffer);
        }
      };
      source.connect(processor.current);
      processor.current.connect(audioContext.current.destination);
    } catch (error) {
      console.error("Error starting recording:", error);
      alert("Could not start recording. Please ensure you have given microphone permissions.");
    }
};


  const stopRecording = () => {
    if (!isRecording) return;
    setIsRecording(false);
    
    if (ws.current) ws.current.close();
    if (processor.current) processor.current.disconnect();
    if (audioContext.current) audioContext.current.close();
    if (stream.current) stream.current.getTracks().forEach(track => track.stop());
  };
  
  useEffect(() => {
    return () => stopRecording();
  }, []);

  return (
    <div className="App">
      <header className="App-header">
        <h1>Real-Time Transcription 🎙️</h1>
        <p>{isRecording ? "Recording in progress..." : "Click start to begin"}</p>
        <div className="button-container">
          <button onClick={startRecording} disabled={isRecording}>Start</button>
          <button onClick={stopRecording} disabled={!isRecording}>Stop</button>
        </div>
        <audio src="YOUR_MP3_URL_HERE.mp3" controls loop>
            Your browser does not support the audio element.
        </audio>
        <p>Play the audio to simulate the AI agent's voice.</p>

        <div className="transcript-container">
          {transcript.map((line, index) => {
            const label = getSpeakerLabel(line.speaker_tag);
            const labelClass = (speakerMap[line.speaker_tag] || '').replace(' ', '').toLowerCase();
            return (
              <p key={index} className={labelClass}>
                <strong>{label}:</strong> {line.text}
              </p>
            );
          })}
        </div>
      </header>
    </div>
  );
}

export default App;
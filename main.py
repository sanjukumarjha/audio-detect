import os
import time
import hmac
import hashlib
import base64
import json
import requests
import shutil
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pydub import AudioSegment

app = FastAPI()

# Input Schema
class AudioRequest(BaseModel):
    audio_url: str
    acr_access_key: str
    acr_access_secret: str

@app.get("/")
def health_check():
    return {"status": "Service is running with FFmpeg"}

@app.post("/identify")
def identify_audio(req: AudioRequest):
    # Temp file names
    temp_wav = f"input_{int(time.time())}.wav"
    temp_mp3 = f"output_{int(time.time())}.mp3"
    
    try:
        # --- STEP 1: DOWNLOAD LARGE FILE ---
        print(f"⬇️ Downloading 65MB+ file from: {req.audio_url}")
        with requests.get(req.audio_url, stream=True) as r:
            r.raise_for_status()
            with open(temp_wav, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        
        file_size_mb = os.path.getsize(temp_wav) / (1024 * 1024)
        print(f"📦 Downloaded Size: {file_size_mb:.2f} MB")

        # --- STEP 2: FFmpeg COMPRESSION (The Magic) ---
        # Convert to: Mono (1ch), 8000Hz, 32k bitrate MP3
        # This turns 65MB WAV -> ~1.5MB MP3 (Full Duration)
        print("⚙️ Compressing with FFmpeg...")
        audio = AudioSegment.from_file(temp_wav)
        
        # Optimize for Speech/Fingerprinting
        audio = audio.set_channels(1).set_frame_rate(8000)
        
        # Export as low-bitrate MP3
        audio.export(temp_mp3, format="mp3", bitrate="32k")
        
        compressed_size_mb = os.path.getsize(temp_mp3) / (1024 * 1024)
        print(f"⚡ Compressed Size: {compressed_size_mb:.2f} MB")

        # --- STEP 3: ACRCLOUD SIGNING ---
        # India/Asia Host
        acr_host = "identify-ap-southeast-1.acrcloud.com"
        http_method = "POST"
        http_uri = "/v1/identify"
        data_type = "audio"
        signature_version = "1"
        timestamp = time.time()

        string_to_sign = f"{http_method}\n{http_uri}\n{req.acr_access_key}\n{data_type}\n{signature_version}\n{str(int(timestamp))}"
        
        sign = base64.b64encode(
            hmac.new(
                req.acr_access_secret.encode('ascii'), 
                string_to_sign.encode('ascii'), 
                digestmod=hashlib.sha1
            ).digest()
        ).decode('ascii')

        # --- STEP 4: UPLOAD ---
        print(f"🚀 Uploading to {acr_host}...")
        files = {'sample': open(temp_mp3, 'rb')}
        data = {
            'access_key': req.acr_access_key,
            'sample_bytes': os.path.getsize(temp_mp3),
            'timestamp': str(int(timestamp)),
            'signature': sign,
            'data_type': data_type,
            'signature_version': signature_version,
            'region': 'IN' # FORCE INDIA REGION
        }

        r = requests.post(f"https://{acr_host}/v1/identify", data=data, files=files)
        response_json = json.loads(r.text)
        
        # Cleanup immediately
        files['sample'].close()
        os.remove(temp_wav)
        os.remove(temp_mp3)

        # --- STEP 5: FILTER SPOTIFY & INDIA ---
        final_matches = []
        status = "no_match"

        if response_json.get('status', {}).get('code') == 0:
            metadata = response_json.get('metadata', {})
            # Combine all sources
            candidates = metadata.get('music', []) + metadata.get('custom_files', [])
            
            for m in candidates:
                # SPOTIFY CHECK
                spotify_id = m.get('external_metadata', {}).get('spotify', {}).get('track', {}).get('id')
                
                if spotify_id:
                    print(f"✅ Found Spotify Match: {m.get('title')}")
                    final_matches.append({
                        "title": m.get('title'),
                        "artist": ", ".join([a['name'] for a in m.get('artists', [])]),
                        "timestamp_match": f"[{format_ms(m.get('play_offset_ms'))}]",
                        "spotify_id": spotify_id,
                        "isrc": m.get('external_ids', {}).get('isrc'),
                        "score": m.get('score')
                    })
            
            if final_matches:
                status = "matched"

        return {"status": status, "data": final_matches, "raw": response_json}

    except Exception as e:
        print(f"🔥 Error: {str(e)}")
        if os.path.exists(temp_wav): os.remove(temp_wav)
        if os.path.exists(temp_mp3): os.remove(temp_mp3)
        raise HTTPException(status_code=500, detail=str(e))

def format_ms(ms):
    if not ms: return "00:00"
    seconds = int((ms / 1000) % 60)
    minutes = int((ms / (1000 * 60)) % 60)
    return f"{minutes:02d}:{seconds:02d}"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)

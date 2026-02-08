import os
import time
import hmac
import hashlib
import base64
import json
import requests
import shutil
import math
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pydub import AudioSegment

app = FastAPI()

class AudioRequest(BaseModel):
    audio_url: str
    acr_access_key: str
    acr_access_secret: str

@app.post("/identify")
def identify_audio(req: AudioRequest):
    temp_wav = f"input_{int(time.time())}.wav"
    temp_mp3 = f"output_{int(time.time())}.mp3"
    
    try:
        # 1. Download & Measure YOUR File
        print(f"⬇️ Downloading: {req.audio_url}")
        with requests.get(req.audio_url, stream=True) as r:
            r.raise_for_status()
            with open(temp_wav, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        
        # Get total duration of YOUR file (for Overlap % calculation)
        source_audio = AudioSegment.from_file(temp_wav)
        total_duration_ms = len(source_audio)
        print(f"📏 User File Duration: {format_ms(total_duration_ms)}")

        # 2. Compress to MP3 (Full Song)
        print("⚙️ Compressing...")
        source_audio.set_channels(1).set_frame_rate(8000).export(temp_mp3, format="mp3", bitrate="32k")

        # 3. ACRCloud Request
        acr_host = "identify-ap-southeast-1.acrcloud.com"
        timestamp = time.time()
        string_to_sign = f"POST\n/v1/identify\n{req.acr_access_key}\naudio\n1\n{str(int(timestamp))}"
        sign = base64.b64encode(hmac.new(req.acr_access_secret.encode('ascii'), string_to_sign.encode('ascii'), digestmod=hashlib.sha1).digest()).decode('ascii')

        files = {'sample': open(temp_mp3, 'rb')}
        data = {
            'access_key': req.acr_access_key,
            'sample_bytes': os.path.getsize(temp_mp3),
            'timestamp': str(int(timestamp)),
            'signature': sign,
            'data_type': 'audio',
            'signature_version': '1',
            'region': 'IN' 
        }

        print(f"🚀 Uploading to {acr_host}...")
        r = requests.post(f"https://{acr_host}/v1/identify", data=data, files=files)
        response = json.loads(r.text)
        
        files['sample'].close()
        os.remove(temp_wav)
        os.remove(temp_mp3)

        # 4. PARSE LIKE SARVINARCK
        final_matches = []
        status = "no_match"

        if response.get('status', {}).get('code') == 0:
            status = "matched"
            metadata = response.get('metadata', {})
            
            # Get ALL matches (Music + Custom Files + Humming)
            all_hits = (
                metadata.get('music', []) + 
                metadata.get('custom_files', []) + 
                metadata.get('humming', [])
            )
            
            for m in all_hits:
                # --- LOGIC MATCHING SARVINARCK ---
                
                # 1. Timestamps: Use 'sample_begin' (Where it matches in YOUR file)
                start_ms = m.get('sample_begin_time_offset_ms', 0)
                end_ms = m.get('sample_end_time_offset_ms', start_ms + m.get('duration_ms', 0))
                
                # 2. Overlap %: (Match Length / Your File Length)
                match_duration = end_ms - start_ms
                if total_duration_ms > 0:
                    overlap_pct = math.ceil((match_duration / total_duration_ms) * 100)
                else:
                    overlap_pct = 0

                # 3. Match Type Logic
                score = m.get('score', 0)
                if score >= 90:
                    match_type = "Exact Match" # or Cover Song if you prefer
                elif score >= 40:
                    match_type = "Remix/Sample" # Sarvinarck shows this for 61%
                else:
                    match_type = "Low Confidence"

                # 4. Extract Metadata
                spotify_data = m.get('external_metadata', {}).get('spotify', {}).get('track', {})
                deezer_data = m.get('external_metadata', {}).get('deezer', {}).get('track', {})
                youtube_data = m.get('external_metadata', {}).get('youtube', {})

                final_matches.append({
                    "title": m.get('title'),
                    "artist": ", ".join([a['name'] for a in m.get('artists', [])]),
                    "type": match_type,
                    "release_date": m.get('release_date', 'Unknown'),
                    # EXACT FORMAT: [01:22 -> 01:30]
                    "time_range": f"[{format_ms(start_ms)} -> {format_ms(end_ms)}]",
                    "match_score": f"{score}%",
                    "overlap_percentage": f"{overlap_pct}%",
                    "isrc": m.get('external_ids', {}).get('isrc', 'N/A'),
                    "label": m.get('label', 'Unknown'),
                    "spotify_id": spotify_data.get('id', None),
                    "deezer_id": deezer_data.get('id', None),
                    "youtube_id": youtube_data.get('vid', None),
                    # Keep raw ID for debugging
                    "acrid": m.get('acrid')
                })

        # Return ALL matches (Don't filter Spotify-only, Sarvinarck shows everything)
        return {"status": status, "data": final_matches}

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

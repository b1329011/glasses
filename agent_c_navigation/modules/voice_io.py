"""
語音輸出入模組
====================================
真實系統：
  - 輸入端：眼鏡麥克風收音 → Whisper API 或手機內建STT轉文字
  - 輸出端：文字 → TTS引擎（語速可調）→ 眼鏡骨傳導喇叭播放

Demo版本：用終端機文字輸入模擬語音輸入、印出文字模擬語音輸出，
         這樣可以先驗證「對話流程」合不合理，不用先處理真實語音。

=== 真實API串接位置（之後替換） ===
    # STT範例（Whisper API）
    import openai
    def speech_to_text(audio_file_path):
        with open(audio_file_path, "rb") as f:
            transcript = openai.Audio.transcribe("whisper-1", f)
        return transcript["text"]

    # TTS範例
    from gtts import gTTS  # 或其他TTS引擎
    def text_to_speech(text, output_path="output.mp3"):
        tts = gTTS(text=text, lang="zh-tw")
        tts.save(output_path)
        # 之後透過藍牙傳給眼鏡骨傳導喇叭播放
"""


def listen() -> str:
    """模擬語音輸入：真實版本是眼鏡麥克風收音+STT，這裡先用文字輸入代替"""
    text = input("🎤 [模擬語音輸入] 你說：")
    return text


def speak(text: str):
    """模擬語音輸出：真實版本是TTS+骨傳導喇叭播放，這裡先印出文字代替"""
    print(f"🔊 [系統語音] {text}")


def speak_wake_prompt():
    speak("我在，請說")

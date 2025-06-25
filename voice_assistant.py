import os
import tempfile
import sounddevice as sd
import soundfile as sf
import numpy as np
import streamlit as st
import time
from typing import Optional, Tuple

# Try to import Deepgram components
try:
    from deepgram import DeepgramClient, PrerecordedOptions
    has_deepgram = True
except ImportError:
    has_deepgram = False

# Try to import speech_recognition as fallback
try:
    import speech_recognition as sr
    has_speech_recognition = True
except ImportError:
    has_speech_recognition = False


class VoiceAssistant:
    def __init__(self):
        """
        Initialize the Voice Assistant with audio recording and transcription capabilities.
        
        Features:
        - Continuous recording (start/stop)
        - Multiple transcription backends (Deepgram preferred, speech_recognition fallback)
        - Audio normalization and proper cleanup
        """
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        self.sample_rate = 16000  # Standard sample rate for speech
        self.channels = 1         # Mono recording
        self.temp_dir = tempfile.gettempdir()
        
        # Recording state
        self.is_recording = False
        self.recording = None
        self.stream = None
        
        # Initialize speech recognition if available
        if has_speech_recognition:
            self.recognizer = sr.Recognizer()
            self.recognizer.energy_threshold = 4000
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.pause_threshold = 0.8  # Slightly longer pause before considering speech ended

    def start_recording(self) -> bool:
        """
        Start audio recording.
        
        Returns:
            bool: True if recording started successfully, False otherwise
        """
        try:
            if self.is_recording:
                st.warning("Recording is already in progress")
                return False
            
            self.is_recording = True
            self.recording = []
            
            def callback(indata: np.ndarray, frames: int, time, status: sd.CallbackFlags):
                """Callback function for audio stream"""
                if self.is_recording:
                    self.recording.append(indata.copy())
            
            # Initialize audio stream
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='float32',
                callback=callback,
                blocksize=2048  # Reasonable block size for voice
            )
            self.stream.start()
            return True
        
        except Exception as e:
            st.error(f"Failed to start recording: {str(e)}")
            self._cleanup_recording()
            return False

    def stop_recording(self) -> Optional[str]:
        """
        Stop recording and save audio to temporary file.
        
        Returns:
            Optional[str]: Path to the saved audio file if successful, None otherwise
        """
        try:
            if not self.is_recording:
                st.warning("No active recording to stop")
                return None
            
            self.is_recording = False
            
            # Stop and close the audio stream
            if self.stream:
                self.stream.stop()
                self.stream.close()
                self.stream = None
            
            # Process recorded audio if we have data
            if self.recording and len(self.recording) > 0:
                # Concatenate all recorded chunks
                audio_data = np.concatenate(self.recording, axis=0)
                
                # Normalize audio to prevent distortion
                max_val = np.max(np.abs(audio_data))
                if max_val > 0:
                    audio_data = audio_data / max_val
                
                # Save as WAV file
                temp_file_path = os.path.join(self.temp_dir, f"recording_{int(time.time())}.wav")
                sf.write(temp_file_path, audio_data, self.sample_rate, subtype='PCM_16')
                
                return temp_file_path
            
            return None
        
        except Exception as e:
            st.error(f"Failed to stop recording: {str(e)}")
            return None
        finally:
            self._cleanup_recording()

    def transcribe_audio(self, audio_file_path: str) -> Optional[str]:
        """
        Transcribe audio using available services (Deepgram preferred, then speech_recognition).
        
        Args:
            audio_file_path: Path to the audio file to transcribe
            
        Returns:
            Optional[str]: Transcribed text if successful, None otherwise
        """
        if not os.path.exists(audio_file_path):
            st.error("Audio file not found for transcription")
            return None
        
        # Try Deepgram first if available
        if has_deepgram and self.deepgram_api_key:
            transcript = self._transcribe_with_deepgram(audio_file_path)
            if transcript:
                return transcript
        
        # Fall back to local speech recognition
        if has_speech_recognition:
            transcript = self._transcribe_with_speech_recognition(audio_file_path)
            if transcript:
                return transcript
        
        st.error("No available transcription method succeeded")
        return None

    def _transcribe_with_deepgram(self, audio_file_path: str) -> Optional[str]:
        """Transcribe audio using Deepgram API"""
        try:
            deepgram = DeepgramClient(self.deepgram_api_key)
            
            with open(audio_file_path, "rb") as file:
                buffer_data = file.read()
            
            payload = {"buffer": buffer_data}
            
            options = PrerecordedOptions(
                model="nova-2",
                smart_format=True,
                language="en-US",
                diarize=False,
                utterances=False
            )
            
            response = deepgram.listen.prerecorded.v("1").transcribe_file(payload, options)
            
            try:
                transcript = response.results.channels[0].alternatives[0].transcript
                return transcript.strip() if transcript else None
            except AttributeError:
                st.error("Unexpected response structure from Deepgram")
                return None
            
        except Exception as e:
            st.error(f"Deepgram transcription failed: {str(e)}")
            return None

    def _transcribe_with_speech_recognition(self, audio_file_path: str) -> Optional[str]:
        """Transcribe audio using local speech recognition"""
        try:
            with sr.AudioFile(audio_file_path) as source:
                # Adjust for ambient noise and read the audio file
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = self.recognizer.record(source)
                
                try:
                    return self.recognizer.recognize_google(audio)
                except sr.UnknownValueError:
                    st.warning("Could not understand audio")
                except sr.RequestError as e:
                    st.error(f"Could not request results: {e}")
        
        except Exception as e:
            st.error(f"Local transcription failed: {str(e)}")
        
        return None

    def process_voice_query(self) -> Optional[str]:
        """
        Complete voice query processing pipeline:
        1. Stops current recording
        2. Saves audio to temporary file
        3. Transcribes the audio
        4. Cleans up temporary files
        
        Returns:
            Optional[str]: Transcribed text if successful, None otherwise
        """
        # Stop recording and get audio file
        audio_file = self.stop_recording()
        
        if not audio_file:
            st.error("No audio was recorded")
            return None
        
        # Transcribe the audio
        with st.spinner("Processing your voice query..."):
            transcript = self.transcribe_audio(audio_file)
            
            # Clean up temporary file
            try:
                os.remove(audio_file)
            except OSError as e:
                st.warning(f"Could not clean up audio file: {str(e)}")
            
            return transcript

    def _cleanup_recording(self):
        """Clean up recording resources"""
        self.is_recording = False
        self.recording = None
        
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except:
                pass
            finally:
                self.stream = None

    @property
    def recording_status(self) -> str:
        """Get current recording status"""
        return "Recording" if self.is_recording else "Not recording"
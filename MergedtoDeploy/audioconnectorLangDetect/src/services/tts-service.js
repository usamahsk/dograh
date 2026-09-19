export class TTSService {
    static beep = [];
    static volumeScale = 0.2; // Scale for volume adjustment (1.0 = full volume)

    // Initialize the beep sound
    static initializeBeep() {
        const sampleRate = 8000; // 8 kHz sample rate
        const frequency = 440;   // A4 note, 440 Hz
        const duration = 0.5;    // Duration in seconds

        const totalSamples = sampleRate * duration;
        const beep = new Array(totalSamples);

        for (let i = 0; i < totalSamples; i++) {
            const sample = TTSService.volumeScale * Math.sin((2 * Math.PI * frequency * i) / sampleRate);
            beep[i] = TTSService.linearToULaw(sample);
        }

        TTSService.beep = beep;
    }

    // μ-law encoding
    static linearToULaw(value) {
        const MAX_VALUE = 32767;
        const MIN_VALUE = -32768;
        const uLawBias = 132;

        const clipped = Math.max(MIN_VALUE, Math.min(MAX_VALUE, value * MAX_VALUE));
        const sign = clipped < 0 ? 0x80 : 0x00;
        const magnitude = Math.min(Math.abs(clipped) + uLawBias, 0x7FFF);

        const exponent = Math.floor(Math.log2(magnitude)) - 7;
        const mantissa = (magnitude >> (exponent + 3)) & 0x0F;

        return ~(sign | (exponent << 4) | mantissa) & 0xFF;
    }

    // Return beep as μ-law encoded bytes
    getAudioBytes(data) {
        if (TTSService.beep.length === 0) {
            TTSService.initializeBeep();
        }
        return Promise.resolve(Uint8Array.from(TTSService.beep));
    }
}

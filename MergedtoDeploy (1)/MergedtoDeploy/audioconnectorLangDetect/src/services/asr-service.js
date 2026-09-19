import { EventEmitter } from 'events';

/*
* This class provides ASR support for the incoming audio from the Client.
* The following events are expected from the session:
* 
*   Name; error
*   Parameters: Error message string or error object.
* 
*   Name: transcript
*   Parameters: `Transcript` object.
* 
*   Name: final-transcript
*   Parameters: `Transcript` object.
* 
* The current usage of this class requires that a new instance be created once
* the final transcript has been received.
*/
export class ASRService {
    constructor() {
        this.emitter = new EventEmitter();
        this.state = 'None';
        this.byteCount = 0;
    }

    on(event, listener) {
        this.emitter.addListener(event, listener);
        return this;
    }

    getState() {
        return this.state;
    }

    /*
    * For this implementation, we are just going to count the number of bytes received.
    * Once we get "enough" bytes, we'll treat this as a completion. In a real-world
    * scenario, an actual ASR engine should be invoked to process the audio bytes.
    */
    processAudio(data) {
        if (this.state === 'Complete') {
            this.emitter.emit('error', 'Speech recognition has already completed.');
            return this;
        }

        this.byteCount += data.length;

        /*
        * If we get enough audio bytes, mark this instance as complete, send out the event,
        * and reset the count to help prevent issues if this instance is attempted to be reused.
        * 
        * 40k bytes equates to 5 seconds of 8khz PCMU audio.
        */
        if (this.byteCount >= 40000) {
            this.state = 'Complete';
            this.emitter.emit('final-transcript', {
                text: 'I would like to check my account balance.',
                confidence: 1.0
            });
            this.byteCount = 0;
            return this;
        }

        this.state = 'Processing';
        return this;
    }
}

class Transcript {
    constructor(text, confidence) {
        this.text = text;
        this.confidence = confidence;
    }
}


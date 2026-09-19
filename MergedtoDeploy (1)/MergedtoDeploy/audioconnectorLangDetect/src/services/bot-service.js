import { TTSService } from "./tts-service.js";

/*
* This class provides support for retreiving a Bot Resource based on the supplied
* connection URL and input variables.
* 
* For the purposes of this example, we are just returning a "dummy" resource, and
* a real implemetation will need to be provided.
*/
export class BotService {
    getBotIfExists(connectionUrl, inputVariables) {
        return Promise.resolve(new BotResource());
    }
}

/*
* This class provides support for the various methods needed to interact with an Bot.
*/
export class BotResource {
    constructor() {
        this.ttsService = new TTSService();
    }

    /*
    * This method is used to retrieve the initial response from the Bot.
    * 
    * This is a "dummy" implementation that will need to be replaced.
    */
    getInitialResponse() {
        const message = 'Hello and welcome to AudioConnector.';

        /*
        * The TTS Service has a "dummy" implementation that will need
        * to be replaced.
        * 
        * See `tts-service` in this folder for more information.
        */
        return this.ttsService.getAudioBytes(message)
            .then(audioBytes => new BotResponse('match', message)
                .withConfidence(1.0)
                .withAudioBytes(audioBytes));
    }

    /*
    * This method is used to retrieve the a response from the Bot
    * based on the provided input. For this implementation, the
    * input is either the Caller's audio's transcript, or captured
    * DTMF digits.
    * 
    * This is a "dummy" implementation that will need to be replaced.
    */
    getBotResponse(data) {
        const message = 'We are unable to help at this time.';

        return this.ttsService.getAudioBytes(message)
            .then(audioBytes => new BotResponse('match', message)
                .withConfidence(1.0)
                .withEndSession(true)
                .withAudioBytes(audioBytes));
    }
}

export class BotResponse {
    constructor(disposition, text) {
        this.disposition = disposition;
        this.text = text;
        this.confidence = undefined;
        this.audioBytes = undefined;
        this.endSession = undefined;
    }

    withConfidence(confidence) {
        this.confidence = confidence;
        return this;
    }

    withAudioBytes(audioBytes) {
        this.audioBytes = audioBytes;
        return this;
    }

    withEndSession(endSession) {
        this.endSession = endSession;
        return this;
    }
}


import { ClientMessage } from '../../../protocol/message.js';
import { DTMFMessage } from '../../../protocol/voice-bots.js';
import { Session } from '../../../common/session.js';
import { MessageHandler } from '../message-handler.js';

export class DTMFMessageHandler {
    handleMessage(message, session) {
        const parsedMessage = message;

        if (!parsedMessage) {
            const message = 'Invalid request parameters.';
            console.log(message);
            session.sendDisconnect('error', message, {});
            return;
        }

        console.log(`Received a DTMF Message. Digit: ${parsedMessage.parameters.digit}`);
        session.processDTMF(parsedMessage.parameters.digit);
    }
}
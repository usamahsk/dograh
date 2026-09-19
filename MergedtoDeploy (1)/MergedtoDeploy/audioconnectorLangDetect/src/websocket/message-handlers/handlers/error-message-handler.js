import { ClientMessage, ErrorMessage } from '../../../protocol/message.js';
import { Session } from '../../../common/session.js';
import { MessageHandler } from '../message-handler.js';

export class ErrorMessageHandler {
    handleMessage(message, session) {
        const parsedMessage = message;

        if (!parsedMessage) {
            return;
        }

        console.log(`Received an Error Message. Code: ${parsedMessage.parameters.code}. Message: ${parsedMessage.parameters.message}`);
    }
}
import { ClientMessage } from '../../../protocol/message.js';
import { Session } from '../../../common/session.js';
import { MessageHandler } from '../message-handler.js';

export class PingMessageHandler {
    handleMessage(message, session) {
        session.send(session.createMessage('pong', {}));
    }
}
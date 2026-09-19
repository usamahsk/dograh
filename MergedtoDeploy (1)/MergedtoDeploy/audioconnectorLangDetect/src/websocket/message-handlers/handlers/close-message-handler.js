import { ClientMessage } from '../../../protocol/message.js';
import { Session } from '../../../common/session.js';
import { MessageHandler } from '../message-handler.js';

export class CloseMessageHandler extends MessageHandler {
    handleMessage(message, session) {
        console.log('Received a Close Message.');
        session.send(session.createMessage('closed', {}));
    }
}

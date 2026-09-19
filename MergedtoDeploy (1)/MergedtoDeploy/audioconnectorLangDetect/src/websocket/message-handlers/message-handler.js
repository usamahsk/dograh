import { ClientMessage } from '../../protocol/message.js';
import { Session } from '../../common/session.js';

/**
 * Base class for message handlers.
 * Subclasses should implement the handleMessage method.
 */
export class MessageHandler {
    /**
     * Handle a client message.
     * @param message
     * @param session
     */
    handleMessage(message, session) {
        throw new Error('handleMessage method must be implemented by subclass');
    }
}
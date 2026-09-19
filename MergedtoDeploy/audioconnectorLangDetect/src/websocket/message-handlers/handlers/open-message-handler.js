import { MediaParameter } from '../../../protocol/core.js';
import {
    ClientMessage,
    OpenMessage,
    ServerMessage
} from '../../../protocol/message.js';
import { Session } from '../../../common/session.js';
import { MessageHandler } from '../message-handler.js';

export class OpenMessageHandler {
    handleMessage(message, session) {
        const parsedMessage = message;

        if (!parsedMessage) {
            const message = 'Invalid request parameters.';
            console.log(message);
            session.sendDisconnect('error', message, {});
            return;
        }

        session.setConversationId(parsedMessage.parameters.conversationId);

        console.log('Received an Open Message.');

        let selectedMedia = null;

        parsedMessage.parameters.media.forEach((element) => {
            if (element.format === 'PCMU' && element.rate === 8000) {
                selectedMedia = element;
            }
        });

        if (!selectedMedia) {
            const message = 'No supported media type was found.';
            console.log(message);
            session.sendDisconnect('error', message, {});
            return;
        }

        console.log(`Using MediaParameter ${JSON.stringify(selectedMedia)}`);

        session.setSelectedMedia(selectedMedia);

        if (parsedMessage.parameters.inputVariables) {
            session.setInputVariables(parsedMessage.parameters.inputVariables);
        }

        session.checkIfBotExists()
            .then((exists) => {
                if (!exists) {
                    const message = 'The specific Bot does not exist.';
                    console.log(message);
                    session.sendDisconnect('error', message, {});
                    return;
                }

                if (selectedMedia) {
                    const response = session.createMessage('opened', {
                        media: [selectedMedia]
                    });

                    session.send(response);
                    session.initializeRealTimeClient();
                }
            });
    }
}
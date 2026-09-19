// session.js -- DEEPGRAM

import { v4 as uuid } from 'uuid';
import { WebSocket } from 'ws';
import dotenv from 'dotenv';

dotenv.config();

import {
    MessageHandlerRegistry
} from '../websocket/message-handlers/message-handler-registry.js';

import {
    BotService
} from '../services/bot-service.js';

import { DTMFService } from '../services/dtmf-service.js';
import { RTSession } from "../services/RealTimeDeepGram.js";

export class Session {

    constructor(ws, sessionId, url) {

        this.ws = ws;

        this.clientSessionId = sessionId;

        this.url = url;

        this.MAXIMUM_BINARY_MESSAGE_SIZE = 64000;

        this.disconnecting = false;

        this.closed = false;

        this.messageHandlerRegistry =
            new MessageHandlerRegistry();

        this.botService =
            new BotService();

        this.dtmfService = null;

        this.conversationId = undefined;

        this.lastServerSequenceNumber = 0;

        this.lastClientSequenceNumber = 0;

        this.inputVariables = {};

        this.selectedMedia = undefined;

        this.selectedBot = null;

        this.isCapturingDTMF = false;

        this.isAudioPlaying = false;

        this.client = null;

        this.utterance = "";

        this.utteranceHistory = [];

        this.audioBuffer = Buffer.alloc(0);
    }

    async close() {

        if (this.closed) return;

        try {

            if (this.client) {

                try {

                    if (
                        typeof this.client.close ===
                        "function"
                    ) {

                        this.client.close();
                    }

                    if (
                        typeof this.client.finish ===
                        "function"
                    ) {

                        this.client.finish();
                    }

                    console.log(
                        'Deepgram session disconnected.'
                    );

                } catch (err) {

                    console.error(
                        'Error disconnecting Deepgram:',
                        err
                    );
                }
            }

            this.ws.close();

        } catch (err) {

            console.error(
                'Error during session close:',
                err
            );
        }

        this.closed = true;
    }

    setConversationId(conversationId) {
        this.conversationId = conversationId;
    }

    setInputVariables(inputVariables) {
        this.inputVariables = inputVariables;
    }

    setSelectedMedia(selectedMedia) {
        this.selectedMedia = selectedMedia;
    }

    setIsAudioPlaying(isAudioPlaying) {
        this.isAudioPlaying = isAudioPlaying;
    }

    processTextMessage(data) {

        if (this.closed) return;

        const message = JSON.parse(data);

        if (
            message.seq !==
            this.lastClientSequenceNumber + 1
        ) {

            console.log(
                `Invalid client sequence number: ${message.seq}.`
            );

            this.sendDisconnect(
                'error',
                'Invalid client sequence number.',
                {}
            );

            return;
        }

        this.lastClientSequenceNumber =
            message.seq;

        if (
            message.serverseq >
            this.lastServerSequenceNumber
        ) {

            console.log(
                `Invalid server sequence number: ${message.serverseq}.`
            );

            this.sendDisconnect(
                'error',
                'Invalid server sequence number.',
                {}
            );

            return;
        }

        if (
            message.id !==
            this.clientSessionId
        ) {

            console.log(
                `Invalid Client Session ID: ${message.id}.`
            );

            this.sendDisconnect(
                'error',
                'Invalid ID specified.',
                {}
            );

            return;
        }

        const handler =
            this.messageHandlerRegistry.getHandler(
                message.type
            );

        if (!handler) {

            console.log(
                `Cannot find a message handler for '${message.type}'.`
            );

            return;
        }

        // REDUCED LOGGING
        // console.log(
        //     "inside text_GB : " +
        //     JSON.stringify(message)
        // );

        handler.handleMessage(message, this);
    }

    createMessage(type, parameters) {

        return {
            id: this.clientSessionId,
            version: '2',
            seq: ++this.lastServerSequenceNumber,
            clientseq: this.lastClientSequenceNumber,
            type,
            parameters
        };
    }

    send(message) {

        if (
            this.ws &&
            this.ws.readyState === WebSocket.OPEN
        ) {

            this.ws.send(
                JSON.stringify(message)
            );
        }
    }

 
sendAudio(bytes) {

    if (
        !this.ws ||
        this.ws.readyState !== WebSocket.OPEN
    ) {
        return;
    }

    // BUFFER SMALL CHUNKS
    if (!this.audioBuffer) {
        this.audioBuffer = Buffer.alloc(0);
    }

    this.audioBuffer =
        Buffer.concat([
            this.audioBuffer,
            bytes
        ]);

    // SEND ONLY WHEN BUFFER LARGE ENOUGH
    const TARGET_SIZE = 3200;

    while (
        this.audioBuffer.length >= TARGET_SIZE
    ) {

        const chunk =
            this.audioBuffer.slice(
                0,
                TARGET_SIZE
            );

        this.audioBuffer =
            this.audioBuffer.slice(
                TARGET_SIZE
            );

        this.ws.send(
            chunk,
            { binary: true }
        );
    }
}



    sendTranscript(
        transcript,
        confidence,
        isFinal
    ) {

        const channel =
            this.selectedMedia?.channels[0];

        if (channel) {

            const parameters = {

                id: uuid(),

                channel,

                isFinal,

                alternatives: [
                    {
                        confidence,

                        interpretations: [
                            {
                                type: 'normalized',
                                transcript
                            }
                        ]
                    }
                ]
            };

            const transcriptEvent = {
                type: 'transcript',
                data: parameters
            };

            const message =
                this.createMessage(
                    'event',
                    {
                        entities: [
                            transcriptEvent
                        ]
                    }
                );

            this.send(message);
        }
    }

    sendDisconnect(
        reason,
        info,
        outputVariables
    ) {

        this.disconnecting = true;

        const disconnectParameters = {
            reason,
            info,
            outputVariables
        };

        const message =
            this.createMessage(
                'disconnect',
                disconnectParameters
            );

        this.send(message);
    }

    sendClosed() {

        const message =
            this.createMessage(
                'closed',
                {}
            );

        this.send(message);
    }

    async initializeRealTimeClient() {

        console.log(
            "initializeClient"
        );

        const rtSession =
            new RTSession();

        let config = {};

        try {

            if (
                this.inputVariables &&
                this.inputVariables.config
            ) {

                config =
                    JSON.parse(
                        this.inputVariables.config
                    );
            }

        } catch (err) {

            console.error(
                "Failed parsing config:",
                err
            );
        }

        const client =
            await rtSession.initializeClient(
                config
            );

        if (!client) {

            console.error(
                "Failed to initialize Deepgram client."
            );

            return;
        }

        this.client = client;

        client.on(
            "message",
            async (message) => {

                // HANDLE BUFFER AUDIO
                if (
                    Buffer.isBuffer(message)
                ) {

                    this.sendAudio(message);

                    return;
                }

                // HANDLE BLOB AUDIO
                if (
                    typeof Blob !== "undefined" &&
                    message instanceof Blob
                ) {

                    const chunk =
                        Buffer.from(
                            await message.arrayBuffer()
                        );

                    this.sendAudio(chunk);

                    return;
                }

                // IGNORE EMPTY OBJECTS
                if (
                    !message ||
                    (
                        typeof message === "object" &&
                        Object.keys(message).length === 0
                    )
                ) {

                    return;
                }

                switch (message.type) {

                    case "ConversationText":

                        // DO NOT SEND TRANSCRIPTS
                        // TO GENESYS
                        if (
                            message.role === "user"
                        ) {

                            console.log(
                                "User Transcript:",
                                message.content
                            );

                            this.utterance =
                                message.content;

                            this.utteranceHistory.push(
                                message.content
                            );
                        }

                        break;

                    case "UserStartedSpeaking":

                        console.log(
                            "User speaking..."
                        );

                        break;

                    case "AgentAudioDone":

                        console.log(
                            "Agent audio completed"
                        );

                        break;

                    default:

                        break;
                }
            }
        );

        client.on(
            "error",
            (err) => {

                console.error(
                    "Deepgram Error:",
                    err
                );
            }
        );

        client.on(
            "close",
            () => {

                console.log(
                    "Deepgram connection closed"
                );

                this.client = null;
            }
        );

        console.log(
            "Client initialized"
        );
    }

    async handleBinaryMessage(data) {

        try {

            if (this.client) {

                this.client.sendMedia(data);
            }

        } catch (error) {

            console.error(
                "Failed sending audio to Deepgram",
                error
            );
        }
    }

    checkIfBotExists() {

        return this.botService
            .getBotIfExists(
                this.url,
                this.inputVariables
            )
            .then(selectedBot => {

                this.selectedBot = selectedBot;

                return this.selectedBot != null;
            });
    }

    async processDTMF(dtmfEvent) {

        console.log(
            "Received DTMF event:",
            dtmfEvent
        );
    }

    logMessage(message) {

        console.log(message);
    }
}
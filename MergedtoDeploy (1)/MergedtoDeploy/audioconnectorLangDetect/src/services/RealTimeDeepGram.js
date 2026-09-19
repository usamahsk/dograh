// RTSession.js

import dotenv from "dotenv";
import { DeepgramClient } from "@deepgram/sdk";

dotenv.config();

export class RTSession {

    client;

    constructor() {
        this.client = null;
    }

    async initializeClient(config = {}) {

        console.log("Initializing Deepgram Agent");

        const deepgram = new DeepgramClient({
            apiKey: process.env.DEEPGRAM_API_KEY
        });

        const connection =
            await deepgram.agent.v1.connect();

        connection.on("open", async () => {

            console.log(
                "Connected to Deepgram Voice Agent"
            );

            await connection.sendSettings({

                type: "Settings",

                audio: {
                    input: {
                        encoding: "mulaw",
                        sample_rate: 8000
                    },

                    output: {
                        encoding: "mulaw",
                        sample_rate: 8000,
                        container: "none"
                    }
                },

                agent: {

                    language: "en",

                    listen: {
                        provider: {
                            type: "deepgram",
                            model: "nova-3"
                        }
                    },

                    think: {
                        provider: {
                            type: "open_ai",
                            model: "gpt-4o-mini"
                        },

                        prompt:
                            config?.prompt ||
                            "You are a helpful reminder assistant."
                    },

                    speak: {
                        provider: {
                            type: "deepgram",
                            model: "aura-2-thalia-en"
                        }
                    },

                    greeting:
                        config?.greeting ||
                        "Hello! How can I help you today?"
                }
            });

            console.log(
                "Deepgram agent configured!"
            );

            setInterval(() => {

                connection.sendKeepAlive({
                    type: "KeepAlive"
                });

            }, 5000);
        });

        connection.on("message", (message) => {

            console.log(
                "Deepgram Message:",
                JSON.stringify(message)
            );
        });

        connection.on("error", (err) => {

            console.error(
                "Deepgram Error:",
                err
            );
        });

        connection.on("close", () => {

            console.warn(
                "Deepgram connection closed"
            );
        });

        await connection.connect();

        await connection.waitForOpen();

        this.client = connection;

        return connection;
    }

    getClient() {
        return this.client;
    }
}
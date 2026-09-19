import { AzureKeyCredential } from "@azure/core-auth";
import { RTClient } from "rt-client";

export class RTSession {
    client;
    MAXIMUM_BINARY_MESSAGE_SIZE = 64000;

    constructor() {
        this.client = this.initializeClient("azure");
    }

    initializeClient(backend) {
        console.log("**Initializing RTClient");

        if (!process.env.AZURE_OPENAI_ENDPOINT || !process.env.AZURE_OPENAI_API_KEY || !process.env.AZURE_OPENAI_DEPLOYMENT) {
            throw new Error("Missing Azure OpenAI configuration in environment variables");
        }

        return new RTClient(
            new URL(process.env.AZURE_OPENAI_ENDPOINT),
            new AzureKeyCredential(process.env.AZURE_OPENAI_API_KEY),
            { deployment: process.env.AZURE_OPENAI_DEPLOYMENT }
        );
    }

    getClient() {
        return this.client;
    }

    handleClientError(error) {
        if (error?.errorDetails?.code === 'session_expired') {
            console.warn("Session expired. Reinitializing RTClient...");
            this.client = this.initializeClient("azure");
        } else {
            console.error("Unhandled RTClient error:", error);
        }
    }
}
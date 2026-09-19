// index.mjs
import express from 'express';
import axios from 'axios';
import path from 'path';
import dotenv from 'dotenv';
import { fileURLToPath } from 'url';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const AZURE_TRANSLATOR_ENDPOINT = process.env.AZURE_TRANSLATOR_ENDPOINT;
const AZURE_TRANSLATOR_KEY = process.env.AZURE_TRANSLATOR_KEY;
const AZURE_TRANSLATOR_REGION = process.env.AZURE_TRANSLATOR_REGION;

const app = express();
app.disable('x-powered-by');

app.use('/', express.static(path.join(__dirname, 'docs')));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.post('/translate', async (req, res) => {
    const body = req.body;
    console.log("gb_body: " + JSON.stringify(body));

    const params = {
        text: body.raw_text,
        from: body.source_language, // source language (optional)
        to: body.target_language     // target language
    };

    const translateURL = `${AZURE_TRANSLATOR_ENDPOINT}/translate?api-version=3.0&to=${params.to}`;

    const headers = {
        'Ocp-Apim-Subscription-Key': AZURE_TRANSLATOR_KEY,
        'Ocp-Apim-Subscription-Region': AZURE_TRANSLATOR_REGION,
        'Content-Type': 'application/json'
    };

    const requestBody = [{ "Text": params.text }];

    try {
        const response = await axios.post(translateURL, requestBody, { headers });

        const translatedText = response.data[0].translations[0].text;

        console.log("Detected Language: " + response.data[0].detectedLanguage.language);

        res.status(200).json({
            source_language: response.data[0].detectedLanguage.language,
            translated_text: translatedText
        });
    } catch (error) {
        console.error('Error during translation:', error);
        res.status(400).json({ error: 'Translation failed', details: error.message });
    }
});

export default app;

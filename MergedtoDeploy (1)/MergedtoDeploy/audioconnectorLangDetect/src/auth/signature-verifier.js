import { createHmac, timingSafeEqual } from 'crypto';
import {
    BareItem,
    Dictionary,
    encodeBareItem,
    encodeInnerList,
    encodeItem,
    InnerList,
    isBoolean,
    isByteSequence,
    isInnerList,
    isInteger,
    isItem,
    isString,
    parseDictionaryField,
} from './structured-fields.js';

// Maximum clock skew we allow between the client and server clock.
const MAX_CLOCK_SKEW = 3;

const derivedComponents = [
    '@method',
    '@authority',
    '@scheme',
    '@target-uri',
    '@request-target',
    '@path',
    '@query',
    '@status',
];

export const withFailure = (code, reason) => ({ code, reason });

export const canonicalizeHeaderFieldValue = (value) => (
    value.trim().replace(/[ \t]*\r\n[ \t]+/g, ' ')
);

export const queryCanonicalizedHeaderField = (headers, name) => {
    const field = headers[name];
    return field
        ? Array.isArray(field)
            ? field.map(canonicalizeHeaderFieldValue).join(', ')
            : canonicalizeHeaderFieldValue(field)
        : null;
};

const querySignatureHeaderField = (headers, name) => {
    const value = headers[name];
    return value ? parseDictionaryField(value) : new Map();
};

const signatureComponentParameterValidator = {
    key: isString,
    name: isString,
    sf: isBoolean,
    bs: isBoolean,
    req: isBoolean,
};

export const verifySignature = async (options) => {
    const {
        headerFields,
        requiredComponents,
        maxSignatureAge,
        signatureSelector,
        derivedComponentLookup,
        keyResolver,
    } = options;

    let signatureInputFields;
    let signatureFields;
    try {
        signatureInputFields = querySignatureHeaderField(headerFields, 'signature-input');
    } catch (err) {
        return withFailure('INVALID', 'Failed to parse "signature-input" header field');
    }
    try {
        signatureFields = querySignatureHeaderField(headerFields, 'signature');
    } catch (err) {
        return withFailure('INVALID', 'Failed to parse "signature" header field');
    }
    if (signatureInputFields.size === 0) {
        if (signatureFields.size === 0) {
            return withFailure('UNSIGNED', 'No "signature" and "signature-input" header fields');
        }
        return withFailure('INVALID', 'Found "signature" but no "signature-input" header field');
    } else if (signatureFields.size === 0) {
        return withFailure('INVALID', 'Found "signature-input" but no "signature" header field');
    }

    const signatures = [];
    for (const [label, signatureBase] of signatureInputFields) {
        const signature = signatureFields.get(label);
        if (!signature) {
            return withFailure('INVALID', `Signature with label ${encodeBareItem(label)} not found`);
        }
        if (!isItem(signature) || !isByteSequence(signature.value)) {
            return withFailure('INVALID', `Invalid "signature" header field value (label: ${encodeBareItem(label)})`);
        }
        if (!isInnerList(signatureBase)) {
            return withFailure('INVALID', `Invalid "signature-input" header field value for label ${encodeBareItem(label)}: (Dictionary member value must be an Inner List)`);
        }

        const components = [];
        for (const { value, params } of signatureBase.value) {
            if (!isString(value)) {
                return withFailure('INVALID', 'Invalid "signature-input" header field value (not an Inner List of Strings)');
            }
            if (params) {
                if (!params.every(({ key, value }) => ((key in signatureComponentParameterValidator) && (signatureComponentParameterValidator[key]?.(value) ?? false)))) {
                    return withFailure('INVALID', `Invalid signature component: ${encodeItem({ value, params })}`);
                }
                components.push({ name: value, params });
            } else {
                components.push({ name: value });
            }
        }

        if (!signatureBase.params) {
            return withFailure('INVALID', 'Invalid "signature-input" header field value (no parameters)');
        }

        const parameters = {};
        for (const { key, value } of signatureBase.params) {
            switch (key) {
                case 'alg':
                    if (!isString(value)) {
                        return withFailure('INVALID', `Invalid "signature-input" header field value (${encodeBareItem(key)} parameter must be a String)`);
                    }
                    parameters.alg = value;
                    break;

                case 'created':
                    if (!isInteger(value) || value < 0) {
                        return withFailure('INVALID', `Invalid "signature-input" header field value (${encodeBareItem(key)} parameter must be an Integer)`);
                    }
                    parameters.created = value;
                    break;

                case 'expires':
                    if (!isInteger(value) || value < 0) {
                        return withFailure('INVALID', `Invalid "signature-input" header field value (${encodeBareItem(key)} parameter must be an Integer)`);
                    }
                    parameters.expires = value;
                    break;

                case 'keyid':
                    if (!isString(value)) {
                        return withFailure('INVALID', `Invalid "signature-input" header field value (${encodeBareItem(key)} parameter must be a String)`);
                    }
                    parameters.keyid = value;
                    break;

                case 'nonce':
                    if (!isString(value)) {
                        return withFailure('INVALID', `Invalid "signature-input" header field value (${encodeBareItem(key)} parameter must be a String)`);
                    }
                    parameters.nonce = value;
                    break;

                default:
                    return withFailure('INVALID', `Invalid "signature-input" header field value (unknown parameter ${encodeBareItem(key)})`);
            }
        }

        signatures.push({
            label,
            parameters,
            components,
            signatureBase,
            signature: signature.value,
        });
    }

    // Select signature
    const label = signatureSelector ? signatureSelector(signatures) : signatures[0].label;
    if (!label) {
        return withFailure('PRECONDITION', 'Multiple signatures and none met selection criteria');
    }
    const selectedSig = signatures.find(x => x.label === label) || signatures[0];
    const {
        parameters,
        components,
        signatureBase,
        signature
    } = selectedSig;

    // Check expiration and created
    if (parameters.created || parameters.expires || maxSignatureAge) {
        const now = options.expirationTimeProvider?.(parameters) ?? (Date.now() / 1000);
        if (parameters.created && (parameters.created > (now + MAX_CLOCK_SKEW))) {
            return withFailure('PRECONDITION', 'Invalid "created" parameter value (time in the future)');
        }
        if (parameters.expires && (parameters.expires < (now + MAX_CLOCK_SKEW))) {
            return withFailure('EXPIRED');
        }
        if (maxSignatureAge) {
            if (!parameters.created) {
                return withFailure('PRECONDITION', 'Cannot determine signature age (no "created" signature parameter)');
            }
            if ((parameters.created + maxSignatureAge) < (now + MAX_CLOCK_SKEW)) {
                return withFailure('EXPIRED');
            }
        }
    }

    // Assemble signature input lines
    const remainingRequired = new Set(requiredComponents);
    const includedComponents = new Set();
    const inputLines = [];
    for (const { name, params } of components) {
        const encoded = encodeItem({ value: name, params });
        let value;
        if (name[0] === '@') {
            if (name === '@signature-params') {
                return withFailure('INVALID', 'The "@signature-params" MUST NOT be listed in covered components.');
            }
            if (name === '@query-params') {
                return withFailure('UNSUPPORTED', `Derived component ${encoded} is not yet supported.`);
            }
            if (!derivedComponents.includes(name)) {
                return withFailure('INVALID', `Unknown derived component (${encoded}) in signature base.`);
            }
            if (params && params.length !== 0) {
                if (params.some(({ key, value }) => (key === 'req') && value)) {
                    return withFailure('UNSUPPORTED', `Related request indicator (req) not yet supported (${encoded}).`);
                }
                return withFailure('INVALID', `Derived component (${encoded}) does not support component parameters.`);
            }
            if (includedComponents.has(encoded)) {
                return withFailure('INVALID', `Duplicate ${encoded} component reference`);
            }
            value = derivedComponentLookup?.(name) || null;
            if (!value && name === '@authority') {
                value = queryCanonicalizedHeaderField(headerFields, 'host');
            }
            if (!value) {
                return withFailure('PRECONDITION', `Cannot resolve reference to ${encoded} component`);
            }
        } else {
            if (name === 'signature') {
                return withFailure('UNSUPPORTED', `Reference to component ${encoded} is not yet supported.`);
            }
            if (params && params.length !== 0) {
                if (params.some(({ key, value }) => (key === 'sf') && value)) {
                    return withFailure('UNSUPPORTED', `Known structured field component parameter (sf) not yet supported (${encoded}).`);
                }
                if (params.some(({ key, value }) => (key === 'bs') && value)) {
                    return withFailure('UNSUPPORTED', `Byte sequence wrapping indicator parameter (bs) not yet supported (${encoded}).`);
                }
                if (params.some(({ key, value }) => (key === 'req') && value)) {
                    return withFailure('UNSUPPORTED', `Related request indicator (req) not yet supported (${encoded}).`);
                }
                return withFailure('INVALID', `Invalid component parameter(s) for component: ${encoded}`);
            }
            const field = queryCanonicalizedHeaderField(headerFields, name);
            if (!field) {
                return withFailure('PRECONDITION', `Header field ${encodeBareItem(name)} not present`);
            }
            value = field;
        }
        inputLines.push(`${encodeItem({ value: name, params })}: ${value}`);
        includedComponents.add(encoded);
        remainingRequired.delete(name);
    }
    if (remainingRequired.size !== 0) {
        return withFailure('PRECONDITION', `Signature does not cover some of the required component(s): ${[...remainingRequired].map(encodeBareItem).join(',')}`);
    }

    inputLines.push(`"@signature-params": ${encodeInnerList(signatureBase)}`);
    const signatureData = inputLines.join('\n');

    const resolverResult = await keyResolver(parameters);
    if (resolverResult.code !== 'GOODKEY' && resolverResult.code !== 'BADKEY') {
        return resolverResult;
    }
    const alg = resolverResult.alg || parameters.alg || 'hmac-sha256';
    const badAlg = (alg !== 'hmac-sha256');
    if (badAlg) {
        return withFailure('UNSUPPORTED', `Unsupported signature algorithm: ${alg}`);
    }

    // HMAC-SHA256 Verification
    if (resolverResult.code === 'GOODKEY') {
        const key = resolverResult.key;
        const computedSig = createHmac('sha256', key).update(signatureData).digest();

        if (!timingSafeEqual(computedSig, signature)) {
            return withFailure('BADSIG', 'Signature did not verify');
        }

        return { code: 'GOODSIG', keyid: parameters.keyid };
    }

    return resolverResult;
};

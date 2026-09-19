/**
 * Types and utility functions to compose and parse structured fields according to RFC8941
 * 
 * @see https://www.rfc-editor.org/rfc/rfc8941.html
 */

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-items
 */
// BareItem = string | number | boolean | symbol | Uint8Array
// (No type definitions in JS)

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-parameters
 */
// Parameter = { key: string; value: BareItem }
// Parameters = Parameter[]

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-items
 */
// Item = { value: BareItem; params?: Parameters }

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-inner-lists
 */
// InnerList = { value: Item[]; params?: Parameters }

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-lists
 */
// ListMember = Item | InnerList
// List = ListMember[]

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-dictionaries
 */
// MemberKey = string
// MemberValue = Item | InnerList
// Dictionary = Map<MemberKey, MemberValue>

const isInnerList = (arg) => (
    Array.isArray(arg.value) && !(arg.value instanceof Uint8Array)
);

const isItem = (arg) => (
    !Array.isArray(arg.value) || (arg.value instanceof Uint8Array)
);

const isString = (arg) => (typeof arg === 'string');

const isBoolean = (arg) => (typeof arg === 'boolean');

const isNumber = (arg) => (typeof arg === 'number');

const isInteger = (arg) => Number.isInteger(arg);

const maybeDecimal = (arg) => (typeof arg === 'number' && !Number.isInteger(arg));

const isToken = (arg) => (typeof arg === 'symbol');

const isByteSequence = (arg) => (arg instanceof Uint8Array);


/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#ser-bare-item
 */
const encodeBareItem = (item) => {
    if (typeof item === 'string') {
        if (/^[\x20-\x7E]*$/.test(item)) {
            return `"${item.replace(/(["\\])/g, '\\$1')}"`;
        }
        throw new RangeError(`Invalid string value (must be ASCII): ${JSON.stringify(item)}`);

    } else if (typeof item === 'number') {
        // Note: the following condition catches NaN and INF too. Don't "simplify"!
        if (-1e15 < item && item < 1e15) {
            if (Number.isInteger(item)) {
                return item.toFixed(0);
            } else if (-1e12 < item && item < 1e12) {
                // Create decimal string representation up to 3 fractional digits, rounded to even.
                const sign = item < 0 ? '-' : '';
                const scaledAbs = Math.abs(item) * 1000;
                if (Math.abs((scaledAbs % 1) - 0.5) < Number.EPSILON) {
                    const tmp = Math.floor(scaledAbs);
                    return `${sign}${((((tmp % 2) === 0) ? tmp : tmp + 1) / 1000).toString()}`;
                } else {
                    return `${sign}${(Math.round(scaledAbs) / 1000).toString()}`;
                }
            }
        }
        throw new RangeError('Invalid numeric value');

    } else if (typeof item === 'boolean') {
        return item ? '?1' : '?0';

    } else if (typeof item === 'symbol') {
        const val = Symbol.keyFor(item);
        if (val && /^[a-zA-Z*][a-zA-Z0-9:/!#$%&'*+\-.^_`|~]*$/.test(val)) {
            return val;
        }
        throw new RangeError(`Invalid symbol/token value: ${JSON.stringify(item)}`);

    } else if (item instanceof Uint8Array) {
        return `:${Buffer.from(item).toString('base64')}:`;

    }
    throw new RangeError(`Invalid/unknown bare item type: '${typeof item}'`);
};

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#ser-key
 */
const encodeKey = (key) => {
    if (/^[a-z*][a-z0-9_\-.*]*$/.test(key)) {
        return key;
    }
    throw new RangeError(`Invalid key: ${JSON.stringify(key)}`);
};

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#ser-params
 */
const encodeParameters = (params) => (
    params.reduce(
        (a, { key, value }) => (
            (value === true) ? (
                `${a};${encodeKey(key)}`
            ) : (
                `${a};${encodeKey(key)}=${encodeBareItem(value)}`
            )
        ),
        ''
    )
);

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-serializing-an-item
 */
const encodeItem = (item) => (
    item.params ? `${encodeBareItem(item.value)}${encodeParameters(item.params)}` : encodeBareItem(item.value)
);

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-serializing-a-list
 */
const encodeList = (list) => (
    list.map(({ value, params }) => (
        (Array.isArray(value) && !(value instanceof Uint8Array)) ? (
            encodeInnerList({ value, params })
        ) : (
            encodeItem({ value, params })
        )
    )).join(', ')
);

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#ser-innerlist
 */
const encodeInnerList = ({ value, params }) => (
    `(${value.map(encodeItem).join(' ')})${params ? encodeParameters(params) : ''}`
);

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-serializing-a-dictionary
 */
const encodeDictionary = (dict) => (
    ((dict instanceof Map) ? [...dict.entries()] : Object.entries(dict))
        .map(([key, { value, params }]) => {
            const k = encodeKey(key);
            if (Array.isArray(value) && !(value instanceof Uint8Array)) {
                return `${k}=${encodeInnerList({ value, params })}`;
            } else if (value === true) {
                return params ? `${k}${encodeParameters(params)}` : k;
            } else {
                return `${k}=${encodeItem({ value, params })}`;
            }
        })
        .join(', ')
);

/**
 * @see https://www.rfc-editor.org/rfc/rfc8941.html#name-serializing-structured-fiel
 */
const encode = (field) => {
    if (field instanceof Map) {
        return encodeDictionary(field);
    } else if (field instanceof Uint8Array) {
        return encodeBareItem(field);
    } else if (Array.isArray(field)) {
        return encodeList(field);
    } else if (typeof field === 'object') {
        return encodeItem(field);
    } else {
        return encodeBareItem(field);
    }
};


const discardOsp = (input) => {
    for (let i = 0; i !== input.length; ++i) {
        const ch = input.charCodeAt(i);
        if (ch !== 0x20) {
            return i === 0 ? input : input.substring(i);
        }
    }
    return '';
};

const discardOws = (input) => {
    for (let i = 0; i !== input.length; ++i) {
        const ch = input.charCodeAt(i);
        if ((ch !== 0x20) && (ch !== 0x09)) {
            return i === 0 ? input : input.substring(i);
        }
    }
    return '';
};

const expectEndOfField = (result) => {
    const rest = discardOsp(result.rest);
    if (rest.length !== 0) {
        throw new Error(`Expect end of field: ${JSON.stringify(rest)}`);
    }
    return result.value;
};

const parse = (input, type) => {
    switch (type) {
        case 'item': return parseItemField(input);
        case 'list': return parseListField(input);
        case 'dictionary': return parseDictionaryField(input);
        default: throw new Error(`Unknown parse field type: ${type}`);
    }
};

const prepareParserInput = (input) => (
    discardOsp(Array.isArray(input) ? input.join(',') : input)
);

const parseListField = (input) => {
    return expectEndOfField(parseList(prepareParserInput(input)));
};

const parseDictionaryField = (input) => {
    return expectEndOfField(parseDictionary(prepareParserInput(input)));
};

const parseItemField = (input) => {
    return expectEndOfField(parseItem(prepareParserInput(input)));
};


const parseList = (input) => {
    const value = [];
    let rest = input;
    if (rest.length === 0) {
        return { value, rest };
    }
    while (true) {
        if (rest[0] === '(') {
            const innerList = parseInnerList(rest);
            rest = innerList.rest;
            value.push(innerList.value);
        } else {
            const item = parseItem(rest);
            rest = item.rest;
            value.push(item.value);
        }
        rest = discardOsp(rest);
        if (rest[0] === ',') {
            rest = discardOsp(rest.substring(1));
        } else {
            break;
        }
    }
    return { value, rest };
};

const parseInnerList = (input) => {
    if (input[0] !== '(') throw new Error('Expect "(" at beginning of inner list');
    const value = [];
    let rest = input.substring(1);
    rest = discardOsp(rest);
    while (rest[0] !== ')') {
        const item = parseItem(rest);
        value.push(item.value);
        rest = discardOsp(item.rest);
    }
    rest = discardOsp(rest.substring(1));
    const params = parseParameters(rest);
    rest = params.rest;
    return { value: { value, params: params.value }, rest };
};

const parseDictionary = (input) => {
    const value = new Map();
    let rest = input;
    if (rest.length === 0) {
        return { value, rest };
    }
    while (rest.length > 0) {
        const key = parseKey(rest);
        rest = key.rest;
        let member;
        if (rest[0] === '=') {
            rest = rest.substring(1);
            if (rest[0] === '(') {
                member = parseInnerList(rest);
                rest = member.rest;
            } else {
                member = parseItem(rest);
                rest = member.rest;
            }
        } else {
            member = { value: true, params: [] };
        }
        value.set(key.value, member.value.params ? member.value : { value: member.value, params: member.params || [] });
        rest = discardOsp(rest);
        if (rest[0] === ',') {
            rest = discardOsp(rest.substring(1));
        } else {
            break;
        }
    }
    return { value, rest };
};

const parseKey = (input) => {
    let i = 0;
    for (; i < input.length; ++i) {
        const ch = input.charCodeAt(i);
        if (
            !(ch === 42 /* * */ || ch === 45 /* - */ || (ch >= 48 && ch <= 57) /* 0-9 */ ||
                (ch >= 97 && ch <= 122) /* a-z */ || ch === 95 /* _ */)
        ) {
            break;
        }
    }
    if (i === 0) {
        throw new Error('Expect at least one character for key');
    }
    return { value: input.substring(0, i), rest: input.substring(i) };
};

const parseItem = (input) => {
    const bareItem = parseBareItem(input);
    const params = parseParameters(bareItem.rest);
    return { value: { value: bareItem.value, params: params.value }, rest: params.rest };
};

const parseBareItem = (input) => {
    if (input.length === 0) {
        throw new Error('Expect bare item, got empty input');
    }
    const ch = input[0];
    if (ch === '"') {
        return parseString(input);
    } else if (ch === ':') {
        return parseBinary(input);
    } else if (ch === '?') {
        return parseBoolean(input);
    } else if (ch === '-' || (ch >= '0' && ch <= '9')) {
        return parseNumber(input);
    } else {
        return parseToken(input);
    }
};

const parseString = (input) => {
    let value = '';
    let escaped = false;
    let i = 1;
    while (i < input.length) {
        const ch = input[i];
        if (escaped) {
            if (ch !== '"' && ch !== '\\') {
                throw new Error('Invalid escape sequence');
            }
            value += ch;
            escaped = false;
        } else if (ch === '\\') {
            escaped = true;
        } else if (ch === '"') {
            return { value, rest: input.substring(i + 1) };
        } else {
            value += ch;
        }
        i++;
    }
    throw new Error('Unterminated string');
};

const parseToken = (input) => {
    let i = 0;
    for (; i < input.length; ++i) {
        const ch = input.charCodeAt(i);
        if (!(
            ch === 42 || ch === 45 || ch === 46 || ch === 95 ||
            (ch >= 48 && ch <= 57) ||
            (ch >= 65 && ch <= 90) ||
            (ch >= 97 && ch <= 122) ||
            ch === 33 || ch === 35 || ch === 36 || ch === 37 ||
            ch === 38 || ch === 39 || ch === 42 || ch === 43 ||
            ch === 94 || ch === 96 || ch === 124 || ch === 126 ||
            ch === 47 || ch === 58
        )) {
            break;
        }
    }
    if (i === 0) {
        throw new Error('Invalid token');
    }
    return { value: Symbol.for(input.substring(0, i)), rest: input.substring(i) };
};

const parseBinary = (input) => {
    if (input[0] !== ':') {
        throw new Error('Expect ":" at beginning of binary');
    }
    let i = 1;
    while (i < input.length && input[i] !== ':') {
        if (!(/[A-Za-z0-9+/=]/).test(input[i])) {
            throw new Error('Invalid character in base64 data');
        }
        i++;
    }
    if (i === input.length) {
        throw new Error('Unterminated base64');
    }
    const data = input.substring(1, i);
    const buffer = Buffer.from(data, 'base64');
    return { value: new Uint8Array(buffer), rest: input.substring(i + 1) };
};

const parseBoolean = (input) => {
    if (input.length < 2 || input[0] !== '?') {
        throw new Error('Invalid boolean');
    }
    if (input[1] === '1') {
        return { value: true, rest: input.substring(2) };
    } else if (input[1] === '0') {
        return { value: false, rest: input.substring(2) };
    }
    throw new Error('Invalid boolean');
};

const parseNumber = (input) => {
    const numberRegex = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,3})?/;
    const match = numberRegex.exec(input);
    if (!match) {
        throw new Error('Invalid number');
    }
    const value = Number(match[0]);
    return { value, rest: input.substring(match[0].length) };
};

const parseParameters = (input) => {
    const value = [];
    let rest = input;
    while (rest.length > 0 && rest[0] === ';') {
        rest = discardOsp(rest.substring(1));
        const key = parseKey(rest);
        rest = key.rest;
        let paramValue = true;
        if (rest[0] === '=') {
            const bare = parseBareItem(rest.substring(1));
            paramValue = bare.value;
            rest = bare.rest;
        }
        value.push({ key: key.value, value: paramValue });
        rest = discardOsp(rest);
    }
    return { value, rest };
};

module.exports = {
    encode,
    encodeBareItem,
    encodeKey,
    encodeParameters,
    encodeItem,
    encodeList,
    encodeInnerList,
    encodeDictionary,
    parse,
    parseListField,
    parseDictionaryField,
    parseItemField,
    isInnerList,
    isItem,
    isString,
    isBoolean,
    isNumber,
    isInteger,
    maybeDecimal,
    isToken,
    isByteSequence
};

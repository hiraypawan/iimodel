'use strict';

const crypto = require('node:crypto');

function luhnValid(candidate) {
  const digits = String(candidate).replace(/\D/g, '');
  if (digits.length < 13 || digits.length > 19 || /^(\d)\1+$/.test(digits)) return false;
  let sum = 0;
  let double = false;
  for (let index = digits.length - 1; index >= 0; index -= 1) {
    let number = Number(digits[index]);
    if (double) {
      number *= 2;
      if (number > 9) number -= 9;
    }
    sum += number;
    double = !double;
  }
  return sum % 10 === 0;
}

function highEntropySecret(value) {
  if (value.length < 24 || value.length > 256) return false;
  if (!/[A-Z]/.test(value) || !/[a-z]/.test(value) || !/\d/.test(value)) return false;
  const frequencies = new Map();
  for (const character of value) frequencies.set(character, (frequencies.get(character) || 0) + 1);
  let entropy = 0;
  for (const count of frequencies.values()) {
    const probability = count / value.length;
    entropy -= probability * Math.log2(probability);
  }
  return entropy >= 4.2;
}

function scanSensitiveText(value) {
  const text = String(value || '').normalize('NFKC');
  const findings = [];
  const checks = [
    ['email', /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i],
    ['indian_phone', /(?:\+?91[\s.-]?)?[6-9]\d{4}[\s.-]?\d{5}\b/],
    ['aadhaar_like_number', /\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/],
    ['indian_pan', /\b[A-Z]{5}\d{4}[A-Z]\b/i],
    ['passport_like_id', /\b[A-Z][1-9]\d{6}\b/i],
    ['ipv4_address', /\b(?:\d{1,3}\.){3}\d{1,3}\b/],
    ['private_key', /-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/],
    ['aws_access_key', /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/],
    ['github_token', /\b(?:gh[opusr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b/],
    ['api_key', /\b(?:sk|pk|api|key|token|secret)[-_][A-Za-z0-9_-]{16,}\b/i],
    ['jwt', /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/],
    ['bearer_token', /\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*/i],
    ['database_url', /\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis):\/\/[^\s]+/i],
    ['password_assignment', /\b(?:password|passwd|pwd|secret)\s*[:=]\s*[^\s,;]{8,}/i],
    ['ifsc_code', /\b[A-Z]{4}0[A-Z0-9]{6}\b/i],
  ];
  for (const [kind, pattern] of checks) {
    if (pattern.test(text)) findings.push(kind);
  }
  const cardCandidates = text.match(/(?:\d[ -]?){13,19}/g) || [];
  if (cardCandidates.some(luhnValid)) findings.push('payment_card');
  const labeledAccount = /\b(?:account|a\/c)\s*(?:number|no\.?|#)?\s*[:=-]?\s*\d{8,18}\b/i;
  if (labeledAccount.test(text)) findings.push('bank_account');
  const genericTokens = text.match(/\b[A-Za-z0-9+/=_-]{24,256}\b/g) || [];
  if (genericTokens.some(highEntropySecret)) findings.push('high_entropy_secret');
  return [...new Set(findings)];
}

function privacyScan(value) {
  const findings = scanSensitiveText(value);
  return {
    schema: 'ii.privacy-scan.v2',
    scanner: 'deterministic-pattern-and-entropy',
    findings,
    passed: findings.length === 0,
    content_sha256: crypto.createHash('sha256').update(String(value || ''), 'utf8').digest('hex'),
    scanned_at: new Date().toISOString(),
  };
}

module.exports = { highEntropySecret, luhnValid, privacyScan, scanSensitiveText };

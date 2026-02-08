export interface ParsedOption {
  id: string;
  label: string;
  description: string;
}

export interface ParsedNarration {
  text: string;
  options: ParsedOption[];
}

/**
 * Parse GM narration text, splitting narrative from options block.
 * Convention: narration...\n\n[选项]\n- label: description\n- label: description
 */
export function parseGMNarration(narration: string): ParsedNarration {
  const marker = '[选项]';
  const idx = narration.indexOf(marker);
  if (idx === -1) return { text: narration.trim(), options: [] };

  const text = narration.slice(0, idx).trim();
  const optionsText = narration.slice(idx + marker.length).trim();
  const lines = optionsText.split('\n').filter(l => l.trim().startsWith('-'));

  const options = lines.map((line, i) => {
    const match = line.match(/^-\s*(.+?):\s*(.+)$/);
    return match
      ? { id: `opt-${i}`, label: match[1].trim(), description: match[2].trim() }
      : { id: `opt-${i}`, label: line.replace(/^-\s*/, '').trim(), description: '' };
  });

  return { text, options };
}

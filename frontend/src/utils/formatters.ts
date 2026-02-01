/**
 * Formatting utility functions
 */

/**
 * Format game time to display string
 */
export function formatGameTime(day: number, hour: number, minute: number): string {
  const hourStr = hour.toString().padStart(2, '0');
  const minuteStr = minute.toString().padStart(2, '0');
  return `Day ${day} ${hourStr}:${minuteStr}`;
}

/**
 * Get time period from hour
 */
export function getTimePeriod(
  hour: number
): 'dawn' | 'day' | 'dusk' | 'night' {
  if (hour >= 5 && hour < 8) return 'dawn';
  if (hour >= 8 && hour < 18) return 'day';
  if (hour >= 18 && hour < 21) return 'dusk';
  return 'night';
}

/**
 * Format HP display
 */
export function formatHP(current: number, max: number): string {
  return `${current}/${max}`;
}

/**
 * Format percentage
 */
export function formatPercent(value: number, total: number): number {
  if (total === 0) return 0;
  return Math.round((value / total) * 100);
}

/**
 * Truncate text with ellipsis
 */
export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength - 3) + '...';
}

/**
 * Format relative time
 */
export function formatRelativeTime(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);

  if (diffSec < 60) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHour < 24) return `${diffHour}h ago`;
  return date.toLocaleDateString();
}

/**
 * Format dice roll result
 */
export function formatDiceRoll(
  rollType: string,
  result: number,
  modifier: number
): string {
  const modStr = modifier >= 0 ? `+${modifier}` : modifier.toString();
  return `${rollType}: ${result}${modStr} = ${result + modifier}`;
}

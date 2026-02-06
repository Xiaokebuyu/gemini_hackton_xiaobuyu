/**
 * Language switch component for i18n
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Globe } from 'lucide-react';

interface LanguageSwitchProps {
  className?: string;
  showLabel?: boolean;
  variant?: 'default' | 'sketch';
}

export const LanguageSwitch: React.FC<LanguageSwitchProps> = ({
  className = '',
  showLabel = true,
  variant = 'default',
}) => {
  const { i18n } = useTranslation();

  const toggleLanguage = () => {
    const newLang = i18n.language === 'zh' ? 'en' : 'zh';
    i18n.changeLanguage(newLang);
  };

  const currentLabel = i18n.language === 'zh' ? '中文' : 'EN';
  const switchToLabel = i18n.language === 'zh' ? 'Switch to English' : '切换到中文';

  if (variant === 'sketch') {
    return (
      <button
        onClick={toggleLanguage}
        className={`
          flex items-center gap-2 px-3 py-1.5
          font-body text-sm
          bg-sketch-bg-panel
          border border-sketch-ink-muted
          rounded-lg
          text-sketch-ink-secondary
          hover:bg-sketch-bg-secondary hover:text-sketch-ink-primary
          hover:border-sketch-ink-secondary
          backdrop-blur-sm
          transition-all duration-200
          ${className}
        `}
        title={switchToLabel}
      >
        <Globe className="w-4 h-4" />
        {showLabel && <span>{currentLabel}</span>}
      </button>
    );
  }

  return (
    <button
      onClick={toggleLanguage}
      className={`
        flex items-center gap-2 px-3 py-1.5
        rounded-lg
        bg-bg-secondary hover:bg-bg-card
        text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]
        transition-colors
        ${className}
      `}
      title={switchToLabel}
    >
      <Globe className="w-4 h-4" />
      {showLabel && (
        <span className="text-sm font-medium">{currentLabel}</span>
      )}
    </button>
  );
};

export default LanguageSwitch;

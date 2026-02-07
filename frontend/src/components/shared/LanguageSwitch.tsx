/**
 * Language switch component for i18n
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Globe } from 'lucide-react';

interface LanguageSwitchProps {
  className?: string;
  showLabel?: boolean;
}

export const LanguageSwitch: React.FC<LanguageSwitchProps> = ({
  className = '',
  showLabel = true,
}) => {
  const { i18n } = useTranslation();

  const toggleLanguage = () => {
    const newLang = i18n.language === 'zh' ? 'en' : 'zh';
    i18n.changeLanguage(newLang);
  };

  const currentLabel = i18n.language === 'zh' ? '中文' : 'EN';
  const switchToLabel = i18n.language === 'zh' ? 'Switch to English' : '切换到中文';

  return (
    <button
      onClick={toggleLanguage}
      className={`
        flex items-center gap-2 px-3 py-1.5
        font-body text-sm
        bg-g-bg-surface
        border border-g-border
        rounded-lg
        text-g-text-secondary
        hover:bg-g-bg-hover hover:text-g-text-primary
        transition-all duration-200
        ${className}
      `}
      title={switchToLabel}
    >
      <Globe className="w-4 h-4" />
      {showLabel && <span>{currentLabel}</span>}
    </button>
  );
};

export default LanguageSwitch;

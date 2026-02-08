/**
 * Quest Panel - Shows narrative progress, current chapter, objectives
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle, Circle, BookOpen } from 'lucide-react';
import { useGameStore } from '../../stores';
import { getNarrativeProgress, getFlowBoard, getCurrentPlan } from '../../api/gameApi';

interface QuestObjectiveView {
  text: string;
  completed: boolean;
}

interface FlowBoardObjective {
  description?: string;
  completed?: boolean;
}

interface FlowBoardStep {
  status?: string;
  objectives?: FlowBoardObjective[];
}

interface FlowBoardData {
  current_chapter?: {
    name?: string;
    description?: string;
  };
  progress?: {
    chapter_total?: number;
    completed_count?: number;
    percentage?: number;
  };
  steps?: FlowBoardStep[];
}

interface CurrentPlanData {
  chapter?: {
    name?: string;
    description?: string;
  } | null;
  goals?: string[];
}

interface NarrativeProgressData {
  chapter_info?: {
    name?: string;
    description?: string;
  };
  chapters_completed?: string[];
}

export const QuestPanel: React.FC = () => {
  const { t } = useTranslation();
  const { worldId, sessionId } = useGameStore();

  const { data: progress, isLoading: progressLoading } = useQuery({
    queryKey: ['narrativeProgress', worldId, sessionId],
    queryFn: () => getNarrativeProgress(worldId!, sessionId!),
    enabled: !!worldId && !!sessionId,
    staleTime: 30000,
  });

  const { data: flowBoard } = useQuery({
    queryKey: ['flowBoard', worldId, sessionId],
    queryFn: () => getFlowBoard(worldId!, sessionId!),
    enabled: !!worldId && !!sessionId,
    staleTime: 30000,
  });

  const { data: currentPlan } = useQuery({
    queryKey: ['currentPlan', worldId, sessionId],
    queryFn: () => getCurrentPlan(worldId!, sessionId!),
    enabled: !!worldId && !!sessionId,
    staleTime: 30000,
  });

  if (!worldId || !sessionId) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-g-text-muted">
        <BookOpen className="w-8 h-8 mb-2" />
        <p className="text-xs">{t('quest.noQuests', '暂无任务')}</p>
      </div>
    );
  }

  if (progressLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="w-5 h-5 border-2 border-g-gold border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  const flowData = (flowBoard || {}) as FlowBoardData;
  const planData = (currentPlan || {}) as CurrentPlanData;
  const progressData = (progress || {}) as NarrativeProgressData;

  const currentStep = (flowData.steps || []).find((step) => step.status === 'current');

  const chapterName =
    planData.chapter?.name ||
    flowData.current_chapter?.name ||
    progressData.chapter_info?.name;
  const chapterDesc =
    planData.chapter?.description ||
    flowData.current_chapter?.description ||
    progressData.chapter_info?.description;

  let objectives: QuestObjectiveView[] = [];
  if (currentStep?.objectives && currentStep.objectives.length > 0) {
    objectives = currentStep.objectives
      .filter((obj) => Boolean(obj.description))
      .map((obj) => ({
        text: obj.description || '',
        completed: Boolean(obj.completed),
      }));
  } else if (Array.isArray(planData.goals) && planData.goals.length > 0) {
    objectives = planData.goals.map((goal) => ({
      text: goal,
      completed: false,
    }));
  }

  const totalChapters = flowData.progress?.chapter_total || 0;
  const chaptersCompleted =
    flowData.progress?.completed_count ||
    (Array.isArray(progressData.chapters_completed)
      ? progressData.chapters_completed.length
      : 0);
  const progressPct = totalChapters > 0
    ? Math.round((chaptersCompleted / totalChapters) * 100)
    : Math.round(flowData.progress?.percentage || 0);

  return (
    <div className="flex flex-col gap-3 p-3 h-full overflow-y-auto g-scrollbar">
      {/* Current chapter */}
      {chapterName && (
        <div>
          <h4 className="text-xs font-heading text-g-gold mb-1">
            {t('quest.currentChapter', '当前章节')}
          </h4>
          <p className="text-sm text-g-text font-semibold">{chapterName}</p>
          {chapterDesc && (
            <p className="text-xs text-g-text-muted mt-1 leading-relaxed">{chapterDesc}</p>
          )}
        </div>
      )}

      {/* Objectives */}
      {objectives.length > 0 && (
        <div>
          <h4 className="text-xs font-heading text-g-gold mb-2">
            {t('quest.objectives', '目标')}
          </h4>
          <ul className="space-y-1.5">
            {objectives.map((obj, i) => (
              <li key={i} className="flex items-start gap-2 text-xs">
                {obj.completed ? (
                  <CheckCircle className="w-3.5 h-3.5 text-green-500 mt-0.5 flex-shrink-0" />
                ) : (
                  <Circle className="w-3.5 h-3.5 text-g-text-muted mt-0.5 flex-shrink-0" />
                )}
                <span className={obj.completed ? 'text-g-text-muted line-through' : 'text-g-text'}>
                  {obj.text}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Progress bar */}
      <div>
        <h4 className="text-xs font-heading text-g-gold mb-1">
          {t('quest.progress', '进度')}
        </h4>
        <div className="flex items-center gap-2">
          <div className="flex-1 h-2 bg-g-bg-dark rounded-full overflow-hidden">
            <div
              className="h-full bg-g-gold rounded-full transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <span className="text-xs text-g-text-muted whitespace-nowrap">
            {chaptersCompleted}/{totalChapters}
          </span>
        </div>
      </div>

      {/* No quests fallback */}
      {!chapterName && objectives.length === 0 && (
        <div className="flex flex-col items-center justify-center py-6 text-g-text-muted">
          <BookOpen className="w-8 h-8 mb-2" />
          <p className="text-xs">{t('quest.noQuests', '暂无任务')}</p>
        </div>
      )}
    </div>
  );
};

export default QuestPanel;

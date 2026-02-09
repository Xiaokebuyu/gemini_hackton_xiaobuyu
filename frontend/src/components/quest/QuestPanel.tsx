/**
 * Quest Panel - Shows narrative progress, current chapter, objectives
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle, Circle, BookOpen } from 'lucide-react';
import { useGameStore } from '../../stores';
import { getNarrativeProgress, getFlowBoard, getCurrentPlan } from '../../api/gameApi';
import type { CoordinatorChapterInfo } from '../../types';

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
    id?: string;
    name?: string;
    description?: string;
    required_events?: string[];
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
    id?: string;
    name?: string;
    description?: string;
  } | null;
  goals?: string[];
  required_events?: string[];
  required_event_summaries?: Array<{
    id?: string;
    name?: string;
    description?: string;
    completed?: boolean;
  }>;
  current_event?: {
    id?: string;
    name?: string;
    description?: string;
  } | null;
}

interface NarrativeProgressData {
  current_chapter?: string;
  chapter_info?: {
    id?: string;
    name?: string;
    description?: string;
  };
  events_triggered?: string[];
  chapters_completed?: string[];
}

export const QuestPanel: React.FC = () => {
  const { t } = useTranslation();
  const { worldId, sessionId, latestChapterInfo } = useGameStore();

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
  const liveChapter = (latestChapterInfo || null) as CoordinatorChapterInfo | null;
  const backendChapterId =
    planData.chapter?.id ||
    flowData.current_chapter?.id ||
    progressData.current_chapter ||
    progressData.chapter_info?.id;
  const trustedLiveChapter = (
    liveChapter &&
    (!backendChapterId || liveChapter.id === backendChapterId)
      ? liveChapter
      : null
  ) as CoordinatorChapterInfo | null;

  const currentStep = (flowData.steps || []).find((step) => step.status === 'current');

  const chapterName =
    trustedLiveChapter?.name ||
    planData.chapter?.name ||
    flowData.current_chapter?.name ||
    progressData.chapter_info?.name;
  const chapterDesc =
    trustedLiveChapter?.description ||
    planData.chapter?.description ||
    flowData.current_chapter?.description ||
    progressData.chapter_info?.description;

  const currentEvent = trustedLiveChapter?.current_event || planData.current_event;
  const taskName = currentEvent?.name || chapterName;
  const taskDesc = currentEvent?.description || chapterDesc;

  const liveEventObjectives = Array.isArray(trustedLiveChapter?.required_event_summaries)
    ? trustedLiveChapter.required_event_summaries
    : [];
  const planEventObjectives = Array.isArray(planData.required_event_summaries)
    ? planData.required_event_summaries
    : [];
  const eventObjectivesSource = liveEventObjectives.length > 0
    ? liveEventObjectives
    : planEventObjectives;

  let objectives: QuestObjectiveView[] = [];
  if (eventObjectivesSource.length > 0) {
    objectives = eventObjectivesSource.map((eventObj) => ({
      text: eventObj.name || eventObj.description || eventObj.id || '',
      completed: Boolean(eventObj.completed),
    })).filter((obj) => obj.text.length > 0);
  } else if (Array.isArray(trustedLiveChapter?.goals) && trustedLiveChapter.goals.length > 0) {
    objectives = trustedLiveChapter.goals.map((goal) => ({
      text: goal,
      completed: false,
    }));
  } else if (currentStep?.objectives && currentStep.objectives.length > 0) {
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
  const requiredEventsFromObjectives = eventObjectivesSource
    .map((eventObj) => eventObj.id)
    .filter((eventId): eventId is string => Boolean(eventId));
  const requiredEvents = Array.isArray(trustedLiveChapter?.required_events)
    ? trustedLiveChapter.required_events
    : Array.isArray(planData.required_events)
      ? planData.required_events
      : requiredEventsFromObjectives.length > 0
        ? requiredEventsFromObjectives
        : Array.isArray(flowData.current_chapter?.required_events)
          ? flowData.current_chapter.required_events
          : [];
  const triggeredEvents = Array.isArray(trustedLiveChapter?.events_triggered)
    ? trustedLiveChapter.events_triggered
    : Array.isArray(progressData.events_triggered)
      ? progressData.events_triggered
      : [];
  const eventTotal = typeof trustedLiveChapter?.event_total === 'number'
    ? trustedLiveChapter.event_total
    : requiredEvents.length;
  const eventCompleted = typeof trustedLiveChapter?.event_completed === 'number'
    ? trustedLiveChapter.event_completed
    : requiredEvents.filter((eventId) => triggeredEvents.includes(eventId)).length;
  const eventPct = eventTotal > 0
    ? (typeof trustedLiveChapter?.event_completion_pct === 'number'
      ? Math.round(trustedLiveChapter.event_completion_pct)
      : Math.round((eventCompleted / eventTotal) * 100))
    : 0;
  const waitingTransition = Boolean(
    trustedLiveChapter?.waiting_transition ||
    (
      eventTotal > 0 &&
      eventCompleted >= eventTotal &&
      !trustedLiveChapter?.transition &&
      !currentEvent
    ),
  );

  return (
    <div className="flex flex-col gap-3 p-3 h-full overflow-y-auto g-scrollbar">
      {/* Current chapter / event */}
      {(taskName || chapterName) && (
        <div>
          {currentEvent && chapterName && (
            <p className="text-[10px] text-g-text-muted mb-0.5">{chapterName}</p>
          )}
          <h4 className="text-xs font-heading text-g-gold mb-1">
            {currentEvent
              ? t('quest.currentTask', '当前任务')
              : t('quest.currentChapter', '当前章节')}
          </h4>
          <p className="text-sm text-g-text font-semibold">{taskName}</p>
          {taskDesc && (
            <p className="text-xs text-g-text-muted mt-1 leading-relaxed">{taskDesc}</p>
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
        <p className="text-[11px] text-g-text-muted mb-1">
          {t('quest.chapterProgress', '章节进度')}
        </p>
        <div className="flex items-center gap-2">
          <div className="flex-1 h-3 bg-g-bg-dark rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-g-gold-dark to-g-gold-light rounded-full transition-all duration-300 shadow-g-gold"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <span className="text-sm font-semibold text-g-text-muted whitespace-nowrap">
            {chaptersCompleted}/{totalChapters}
          </span>
        </div>
        {eventTotal > 0 && (
          <>
            <p className="text-[11px] text-g-text-muted mt-2 mb-1">
              {t('quest.eventProgress', '事件进度')}
            </p>
            <div className="flex items-center gap-2">
              <div className="flex-1 h-2 bg-g-bg-dark rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 rounded-full transition-all duration-300"
                  style={{ width: `${eventPct}%` }}
                />
              </div>
              <span className="text-xs text-g-text-muted whitespace-nowrap">
                {eventCompleted}/{eventTotal}
              </span>
            </div>
          </>
        )}
        {waitingTransition && (
          <p className="text-[11px] text-g-text-muted mt-2">
            {t('quest.waitingTransition', '章节事件已完成，正在进入下一章节...')}
          </p>
        )}
      </div>

      {/* No quests fallback */}
      {!taskName && !chapterName && objectives.length === 0 && (
        <div className="flex flex-col items-center justify-center py-6 text-g-text-muted">
          <BookOpen className="w-8 h-8 mb-2" />
          <p className="text-xs">{t('quest.noQuests', '暂无任务')}</p>
        </div>
      )}
    </div>
  );
};

export default QuestPanel;

const SHANGHAI_TIME_ZONE = 'Asia/Shanghai';

const parseDate = (value?: string | null) => {
  if (!value) return null;
  const normalized = /z$|[+-]\d{2}:\d{2}$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
};

export const formatChinaTime = (value?: string | null) => {
  const date = parseDate(value);
  if (!date) return '时间未知';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
};

export const formatChinaDateTime = (value?: string | null) => {
  const date = parseDate(value);
  if (!date) return '时间未知';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
};

export const formatSessionTime = (value?: string | null) => {
  const date = parseDate(value);
  if (!date) return '时间未知';
  const today = new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
  const target = new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date);
  if (target === today) {
    return `今天 ${formatChinaTime(value)}`;
  }
  return formatChinaDateTime(value);
};

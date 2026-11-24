export const $ = id => document.getElementById(id);
export const list = value => Array.isArray(value) ? value : [];
export const node = (tag, text = '', className = '') => {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
};
export const time = value => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC') : '未报告';

export function svg(tag, attributes = {}) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

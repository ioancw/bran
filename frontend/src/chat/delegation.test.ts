import { describe, it, expect } from 'vitest'
import { delegationLabel, parseTaskNotification } from './delegation'

describe('delegationLabel', () => {
  it('names an inline subagent', () => {
    expect(
      delegationLabel('Agent', { subagent_type: 'research', description: 'Find the base rate' }),
    ).toEqual({
      label: 'Delegated to research',
      detail: 'Find the base rate',
    })
    expect(delegationLabel('Task', { prompt: 'Do it' }).label).toBe('Delegated to general-purpose')
    expect(delegationLabel('Agent', {}, 'finance-news').label).toBe('Delegated to finance-news')
  })
  it('names a workflow, from the call or from the run recorded for it', () => {
    expect(delegationLabel('Workflow', { name: 'fact-check', args: { path: '/b/brief.md' } })).toEqual({
      label: 'Started workflow fact-check',
      detail: '/b/brief.md',
    })
    expect(delegationLabel('Workflow', {}, 'workflow:fact-check').label).toBe('Started workflow fact-check')
    expect(delegationLabel('Workflow', {}).label).toBe('Started workflow a workflow')
  })
  it('names a background run', () => {
    expect(
      delegationLabel('mcp__bran__spawn_agent', { agent: 'research', task: 'ECB deposit rate' }),
    ).toEqual({
      label: 'Started background run: research',
      detail: 'ECB deposit rate',
    })
  })
})

describe('parseTaskNotification', () => {
  it('reads the notice the SDK sends when background work finishes', () => {
    const text =
      '<task-notification>\n<task-id>w4</task-id>\n<tool-use-id>toolu_01A</tool-use-id>\n' +
      '<status>completed</status>\n<summary>Dynamic workflow "Verify claims" completed</summary>\n' +
      '<result>{"a":1}</result>'
    expect(parseTaskNotification(text)).toEqual({
      toolUseId: 'toolu_01A',
      status: 'completed',
      summary: 'Dynamic workflow "Verify claims" completed',
    })
  })
  it('is null for what a person wrote, and tolerant of missing fields', () => {
    expect(parseTaskNotification('Finance news')).toBeNull()
    expect(parseTaskNotification('please explain <task-notification> to me')).toBeNull()
    expect(parseTaskNotification('<task-notification></task-notification>')).toEqual({
      toolUseId: null,
      status: 'completed',
      summary: 'A background task finished',
    })
  })
})

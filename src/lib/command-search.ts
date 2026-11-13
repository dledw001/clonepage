type SearchableCommand = { name:string; aliases:string[] };
export function filterCommands<T extends SearchableCommand>(commands:T[],query:string):T[] {
  const text=query.trim().toLowerCase();
  return text?commands.filter(command=>command.name.toLowerCase().includes(text)||command.aliases.some(alias=>alias.toLowerCase().includes(text))):commands;
}
/** -1 represents the search input, followed by zero-based result positions. */
export function nextCommandIndex(index:number,direction:1|-1,count:number):number {
  if(count<=0)return -1;
  return Math.max(-1,Math.min(index+direction,count-1));
}

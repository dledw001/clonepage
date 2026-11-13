export type WeatherUnits = 'F' | 'C';
export type WeatherPreferences = { location:string; units:WeatherUnits };
export type WeatherTemperature = { c:number|null; f:number|null };
export type WeatherDay = { date:string; high:WeatherTemperature; low:WeatherTemperature; description:string|null; code:string; rain_chance:number|null };
export type WeatherSnapshot = { location:string; units:WeatherUnits; place:string; country:string|null; fetched_at:string|null; stale:boolean; error:string|null;
  current:{temperature:WeatherTemperature;feels_like:WeatherTemperature;description:string;code:string;wind_kph:number|null;wind_mph:number|null;wind_direction:string;humidity:number|null}|null; forecast:WeatherDay[] };

export function temperatureText(value:WeatherTemperature|undefined, units:WeatherUnits):string {
  const number=units==='F'?value?.f:value?.c;
  return typeof number==='number'&&Number.isFinite(number)?`${Math.round(number)||0}°${units}`:'—';
}
export function windText(current:NonNullable<WeatherSnapshot['current']>,units:WeatherUnits):string {
  const value=units==='F'?(current.wind_mph??(current.wind_kph===null?null:current.wind_kph/1.609344)):(current.wind_kph??(current.wind_mph===null?null:current.wind_mph*1.609344));
  return value!==null&&Number.isFinite(value)?`${Math.round(value)} ${units==='F'?'mph':'km/h'}${current.wind_direction?` ${current.wind_direction}`:''}`:'—';
}
export function forecastDate(value:string):string {
  // Calendar dates belong to the forecast location. Noon avoids UTC date shifts.
  const date=new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime())?value:date.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'});
}
export function weatherKind(code:string, description='',forecast=false):'sun'|'moon'|'partly'|'cloud'|'fog'|'rain'|'snow'|'storm' {
  // WorldWeatherOnline condition codes used by wttr.in:
  // https://www.worldweatheronline.com/weather-api/api/docs/weather-icons.aspx
  const value=Number(code);
  if(value===113)return !forecast&&/clear/i.test(description)?'moon':'sun';
  if(value===116)return 'partly';
  if([143,248,260].includes(value))return 'fog';
  if([200,386,389,392,395].includes(value))return 'storm';
  if([179,182,185,227,230,281,284,311,314,317,320,323,326,329,332,335,338,350,362,365,368,371,374,377].includes(value))return 'snow';
  if([176,263,266,293,296,299,302,305,308,353,356,359].includes(value))return 'rain';
  return 'cloud';
}

export function weatherUrl(location:string,units:WeatherUnits):string {
  const query=/^\d{5}(?:-\d{4})?$/.test(location)?`${location}, United States`:location;
  return `https://wttr.in/${encodeURIComponent(query)}?${units==='F'?'u':'m'}`;
}

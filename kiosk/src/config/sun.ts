const SUN_LAT = 61.4978;
const SUN_LNG = 23.7610;
const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

function getSunHour(doy: number, rising: boolean): number {
  var lngH = SUN_LNG / 15;
  var t = doy + ((rising ? 6 : 18) - lngH) / 24;
  var M = 0.9856 * t - 3.289;
  var L = ((M + 1.916 * Math.sin(M * D2R) + 0.020 * Math.sin(2 * M * D2R) + 282.634) % 360 + 360) % 360;
  var RA = ((R2D * Math.atan(0.91764 * Math.tan(L * D2R))) % 360 + 360) % 360;
  RA += Math.floor(L / 90) * 90 - Math.floor(RA / 90) * 90;
  RA /= 15;
  var sinDec = 0.39782 * Math.sin(L * D2R);
  var cosDec = Math.cos(Math.asin(sinDec));
  var cosH = (Math.cos(90.833 * D2R) - sinDec * Math.sin(SUN_LAT * D2R)) / (cosDec * Math.cos(SUN_LAT * D2R));
  if (cosH > 1) return rising ? 99 : -99;
  if (cosH < -1) return rising ? -99 : 99;
  var H = R2D * Math.acos(cosH);
  if (rising) H = 360 - H;
  H /= 15;
  var ut = ((H + RA - 0.06571 * t - 6.622 - lngH) % 24 + 24) % 24;
  return ut + (-new Date().getTimezoneOffset() / 60);
}

export function isDaytime(): boolean {
  var now = new Date();
  var start = new Date(now.getFullYear(), 0, 1);
  var doy = Math.floor((now.getTime() - start.getTime()) / 86400000) + 1;
  var h = now.getHours() + now.getMinutes() / 60;
  return h >= getSunHour(doy, true) && h < getSunHour(doy, false);
}

export function grafanaTheme(): 'light' | 'dark' {
  return isDaytime() ? 'light' : 'dark';
}

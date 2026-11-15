import copy
import datetime as dt
import unittest
from weather_data import weather_payload


class WeatherDataTests(unittest.TestCase):
    def setUp(self):
        self.today=dt.date(2026,11,7)
        self.daily={'time':[(self.today+dt.timedelta(days=i)).isoformat() for i in range(-7,2)],
                    'temperature_2m_max':[10,12,14,16,18,20,22,50,30],
                    'temperature_2m_min':[0,2,4,6,8,10,12,40,20],
                    'weather_code':[3]*8+[61]}

    def test_model_history_is_never_accepted_as_observed_baseline(self):
        result=weather_payload({'daily':self.daily},self.today)
        baseline=result['baseline']
        self.assertFalse(baseline['available'])
        self.assertEqual(baseline['sampleCount'],0)
        self.assertNotIn('highMean',baseline)
        self.assertEqual(result['daily']['time'],['2026-11-07','2026-11-08'])
        self.assertEqual(result['daily']['temperature_2m_max'],[50,30])

    def test_historical_null_missing_and_invalid_temperature_keep_forecast(self):
        for value in (None,float('nan'),float('inf'),True,100):
            daily=copy.deepcopy(self.daily);daily['temperature_2m_max'][2]=value
            result=weather_payload({'daily':daily},self.today)
            self.assertFalse(result['baseline']['available'])
            self.assertEqual(result['daily']['weather_code'],[3,61])
        daily={k:v[1:] for k,v in self.daily.items()}
        self.assertFalse(weather_payload({'daily':daily},self.today)['baseline']['available'])

    def test_old_two_day_response_does_not_invent_a_week(self):
        daily={k:v[-2:] for k,v in self.daily.items()}
        result=weather_payload({'daily':daily},self.today)
        self.assertTrue(result['available']);self.assertFalse(result['baseline']['available'])

    def test_duplicate_dates_missing_forecast_and_invalid_current_values_rejected(self):
        for field,value in [('temperature_2m_min',None),('temperature_2m_max',float('nan')),('weather_code',True)]:
            daily=copy.deepcopy(self.daily);daily[field][-1]=value
            with self.assertRaises(ValueError):weather_payload({'daily':daily},self.today)
        daily=copy.deepcopy(self.daily);daily['time'][-1]='2000-01-01'
        with self.assertRaises(ValueError):weather_payload({'daily':daily},self.today)
        daily=copy.deepcopy(self.daily);daily['time'][0]=daily['time'][1]
        with self.assertRaises(ValueError):weather_payload({'daily':daily},self.today)

    def test_even_complete_plausible_model_history_remains_unverified(self):
        daily=copy.deepcopy(self.daily);daily['temperature_2m_max'][:7]=[20]*6+[21]
        result=weather_payload({'daily':daily},self.today)
        self.assertFalse(result['baseline']['available'])
        self.assertEqual(result['weatherVersion'],3)


if __name__=='__main__':unittest.main()

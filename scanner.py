import os
import requests
import time
from datetime import datetime, timedelta
import warnings
import pytz

# Suppress SSL warnings
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

# Configuration from environment variables
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
FOOTBALL_DATA_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Top league competitions (Football-Data.org IDs)
TOP_LEAGUES = {
    "PL": 2021,     # Premier League
    "LaLiga": 2014, # La Liga
    "Bundesliga": 2002,  # Bundesliga
    "Serie A": 2019,     # Serie A
    "Ligue 1": 2015,     # Ligue 1
    "UCL": 2001,         # Champions League
    "UEL": 2000,         # Europa League
    "MLS": 2142          # MLS (example secondary league)
}

HEADERS = {
    "X-RapidAPI-Key": RAPIDAPI_KEY,
    "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"
}

FOOTBALL_DATA_HEADERS = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}

def debug_log(message):
    timestamp = datetime.now(pytz.timezone('Europe/London')).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[DEBUG][{timestamp}] {message}")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, verify=False, timeout=10)
        debug_log(f"Telegram response: {response.status_code}")
        return response.json()
    except Exception as e:
        debug_log(f"Telegram send error: {str(e)[:100]}")
        return None

def get_fixtures(date):
    """Get today's fixtures from football-data.org for TOP leagues only"""
    all_fixtures = []
    
    # Check each top league separately to avoid missing data
    for league_name, league_id in TOP_LEAGUES.items():
        url = f"https://api.football-data.org/v4/matches?date={date}&competitions={league_id}"
        debug_log(f"FootballData REQ: {url}")
        
        try:
            response = requests.get(url, headers=FOOTBALL_DATA_HEADERS, timeout=15)
            debug_log(f"FootballData RESP: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                matches = data.get('matches', [])
                debug_log(f"Found {len(matches)} fixtures in {league_name}")
                
                for fixture in matches:
                    try:
                        # Extract fixture data
                        fixture_data = {
                            "id": fixture['id'],
                            "league": fixture['competition']['name'],
                            "home": fixture['homeTeam']['name'],
                            "away": fixture['awayTeam']['name'],
                            "home_id": fixture['homeTeam']['id'],
                            "away_id": fixture['awayTeam']['id'],
                            "date": fixture['utcDate'],
                            "competition_id": fixture['competition']['id']
                        }
                        all_fixtures.append(fixture_data)
                        debug_log(f"Added {league_name} fixture: {fixture_data['home']} vs {fixture_data['away']}")
                    except KeyError as e:
                        debug_log(f"Skipping fixture due to missing data: {str(e)}")
            elif response.status_code == 403:
                debug_log("API rate limit exceeded or access denied")
                break
            elif response.status_code == 404:
                debug_log(f"No data available for {league_name} on {date}")
            else:
                debug_log(f"FootballData Error for {league_name}: {response.text[:200]}")
            
            # Respect API rate limits (10 requests per minute)
            time.sleep(10)  # 6 seconds between league requests
            
        except Exception as e:
            debug_log(f"FootballData Exception for {league_name}: {str(e)}")
    
    # Sort by league importance and limit to 20 fixtures max
    priority_leagues = [2021, 2014, 2002, 2019, 2015, 2001, 2000]  # Priority order
    all_fixtures.sort(key=lambda x: priority_leagues.index(x['competition_id']) 
                      if x['competition_id'] in priority_leagues else 999)
    
    debug_log(f"Total top league fixtures found: {len(all_fixtures)}")
    return all_fixtures[:20]  # Limit to 20 most important fixtures

def get_team_history(team_id, is_home, opponent_id=None):
    """Get team form with flexible data source"""
    # First try football-data.org
    url = f"https://api.football-data.org/v4/teams/{team_id}/matches"
    params = {
        "status": "FINISHED",
        "limit": 10,
        "dateFrom": (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d"),
        "dateTo": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    }
    
    try:
        response = requests.get(url, headers=FOOTBALL_DATA_HEADERS, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            matches = data.get('matches', [])
            
            # Filter for home/away matches
            venue_matches = []
            for match in matches:
                if is_home and match['homeTeam']['id'] == team_id:
                    venue_matches.append(match)
                elif not is_home and match['awayTeam']['id'] == team_id:
                    venue_matches.append(match)
            
            # If we have opponent-specific request, filter H2H
            if opponent_id:
                h2h_matches = [m for m in venue_matches 
                              if opponent_id in (m['homeTeam']['id'], m['awayTeam']['id'])]
                return h2h_matches[:5]  # Return last 5 H2H
            
            return venue_matches[:5]  # Return last 5 venue-specific matches
    except:
        pass
    
    # Fallback to RapidAPI if football-data fails
    debug_log(f"Using RapidAPI fallback for team {team_id}")
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
    venue = "home" if is_home else "away"
    params = {"team": team_id, "last": 5, "status": "finished", "venue": venue}
    
    try:
        response = requests.get(url, headers=HEADERS, params=params, verify=False, timeout=8)
        if response.status_code == 200:
            data = response.json()
            return data.get('response', [])
    except:
        pass
    
    return []

def analyze_fixture(fixture):
    """Improved prediction logic with flexible thresholds"""
    predictions = []
    
    # Get required data with football-data first
    h2h_matches = get_team_history(fixture['home_id'], True, fixture['away_id'])
    home_form = get_team_history(fixture['home_id'], True)
    away_form = get_team_history(fixture['away_id'], False)
    
    debug_log(f"Found: {len(h2h_matches)} H2H, {len(home_form)} home form, {len(away_form)} away form")
    
    # Rule 1: H2H Dominance (W1/W2) - require at least 3 matches
    if len(h2h_matches) >= 3:
        home_wins = 0
        away_wins = 0
        
        for match in h2h_matches:
            if match['homeTeam']['id'] == fixture['home_id']:
                if match['score']['fullTime']['home'] > match['score']['fullTime']['away']:
                    home_wins += 1
                elif match['score']['fullTime']['home'] < match['score']['fullTime']['away']:
                    away_wins += 1
        
        if home_wins >= 3:
            predictions.append(f"W1 (H2H: {home_wins}/{len(h2h_matches)} wins)")
        elif away_wins >= 3:
            predictions.append(f"W2 (H2H: {away_wins}/{len(h2h_matches)} wins)")
    
    # Rule 2: Home/Away Form (W1/W2) - require at least 3 matches
    if len(home_form) >= 3:
        home_wins = sum(1 for m in home_form 
                      if m['homeTeam']['id'] == fixture['home_id'] and 
                         m['score']['fullTime']['home'] > m['score']['fullTime']['away'])
        
        if home_wins >= 3:
            predictions.append(f"W1 (Home Form: {home_wins}/{len(home_form)} wins)")
    
    if len(away_form) >= 3:
        away_wins = sum(1 for m in away_form 
                      if m['awayTeam']['id'] == fixture['away_id'] and 
                         m['score']['fullTime']['away'] > m['score']['fullTime']['home'])
        
        if away_wins >= 3:
            predictions.append(f"W2 (Away Form: {away_wins}/{len(away_form)} wins)")
    
    # Rule 3: Both Teams to Score (BTS)
    if len(h2h_matches) >= 3:
        bts_count = sum(1 for m in h2h_matches 
                      if m['score']['fullTime']['home'] > 0 and m['score']['fullTime']['away'] > 0)
        
        if bts_count >= 3:
            predictions.append(f"BTS (H2H: {bts_count}/{len(h2h_matches)} matches)")
    
    # Rule 4: Over 2.5 Goals
    if len(h2h_matches) >= 3:
        over_count = sum(1 for m in h2h_matches 
                       if m['score']['fullTime']['home'] + m['score']['fullTime']['away'] > 2.5)
        
        if over_count >= 3:
            predictions.append(f"Over 2.5 (H2H: {over_count}/{len(h2h_matches)} matches)")
    
    return predictions

def get_next_match_date():
    """Find the next date with actual football matches"""
    uk_tz = pytz.timezone('Europe/London')
    today = datetime.now(uk_tz)
    
    # Check next 7 days for matches
    for days_ahead in range(0, 7):
        check_date = today + timedelta(days=days_ahead)
        formatted_date = check_date.strftime("%Y-%m-%d")
        
        # Quick check if any top leagues have matches on this date
        for league_id in TOP_LEAGUES.values():
            url = f"https://api.football-data.org/v4/matches?date={formatted_date}&competitions={league_id}"
            try:
                response = requests.get(url, headers=FOOTBALL_DATA_HEADERS, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('matches') and len(data['matches']) > 0:
                        debug_log(f"Found matches on {formatted_date}")
                        return formatted_date
            except:
                continue
                
        time.sleep(2)  # Rate limiting
    
    # If no matches found in next 7 days, return today's date
    return today.strftime("%Y-%m-%d")

def main():
    # Get current UK time
    uk_tz = pytz.timezone('Europe/London')
    now_uk = datetime.now(uk_tz)
    
    # Find the next date with actual matches
    match_date = get_next_match_date()
    
    debug_log(f"======== STARTING SCAN FOR {match_date} ========")
    debug_log(f"Current UK time: {now_uk.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    fixtures = get_fixtures(match_date)
    
    if not fixtures:
        debug_log("No fixtures found, sending Telegram alert")
        send_telegram("⛔ <b>No Top League Fixtures Found</b>\n\nNo matches scheduled in top leagues for the next week.")
        return
    
    debug_log(f"Processing {len(fixtures)} top league fixtures for predictions")
    signals = []
    
    for i, fixture in enumerate(fixtures):
        debug_log(f"Analyzing {i+1}/{len(fixtures)}: {fixture['home']} vs {fixture['away']}")
        try:
            predictions = analyze_fixture(fixture)
            if predictions:
                match_time = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00')).astimezone(uk_tz)
                match_info = (
                    f"<b>🏟 {fixture['home']} vs {fixture['away']}</b>\n"
                    f"<b>League:</b> {fixture['league']}\n"
                    f"<b>Date:</b> {match_time.strftime('%Y-%m-%d')}\n"
                    f"<b>Time:</b> {match_time.strftime('%H:%M %Z')}\n"
                    f"<b>Predictions:</b>\n" + "\n".join([f"• {pred}" for pred in predictions])
                )
                signals.append(match_info)
                debug_log(f"Signal found: {predictions}")
            else:
                debug_log("No predictions met criteria")
        except Exception as e:
            debug_log(f"Analysis error: {str(e)}")
        time.sleep(1.5)  # Rate limiting
    
    if signals:
        message = (
            "⚽ <b>TOP LEAGUE PREDICTION SIGNALS</b> ⚽\n"
            f"<b>Match Date:</b> {match_date}\n"
            f"<b>Report Generated:</b> {now_uk.strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"<b>Total Signals:</b> {len(signals)}/{len(fixtures)}\n\n"
            + "\n\n".join(signals) +
            "\n\n<i>Disclaimer: Predictions based on historical data analysis. Past performance doesn't guarantee future results.</i>"
        )
        send_telegram(message)
        debug_log(f"Sent {len(signals)} signals to Telegram")
    else:
        send_telegram(
            f"ℹ️ <b>No Prediction Signals Found</b>\n\n"
            f"<b>Match Date:</b> {match_date}\n"
            f"<b>Scanned:</b> {len(fixtures)} top league fixtures\n"
            f"<b>Time:</b> {now_uk.strftime('%H:%M %Z')}\n\n"
            "No top league matches met the prediction criteria."
        )
        debug_log("No signals found")
    
    debug_log("======== SCAN COMPLETED ========")

if __name__ == "__main__":
    main()

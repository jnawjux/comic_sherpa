from selenium.webdriver import Chrome
import time
import pandas as pd
import pyspark
import pyspark.sql.functions as F
from pyspark.ml.recommendation import ALS
from pyspark.sql.types import StringType, IntegerType
import urllib



from IPython.display import clear_output

def get_amazon_list_ids(link):
    """Scraping function using Selenium for getting Amazon product IDs (ASIN) from 'Best Seller' pages.
    Args:
      link: html link of 'Best Sellers' page. 
    Returns:
      all_ids: a list of ASIN ids found on page. 
    """
    # Instantiate Chrome and open to link
    browser = Chrome()
    browser.get(link)

    # Grabs the links from the page, then seperates out the ASIN from the link for each product
    all_ids = [x.get_attribute('href').split('dp/')[1].split('/')[0] 
        for x in browser.find_elements_by_xpath('//*[@id="zg-ordered-list"]/li/span/div/span/a')]

    return all_ids


def get_related_ids(df):
    """Function to unpack a list of the unique related product ASINs from the 'Also bought' section.
    Args:
        df: Pandas dataframe (specifically from metadata.json as it works with its schema) 
    Returns:
        all_unique_ids: Python list of unique ASINs from a dataframe that are in the 'related' field
    """

    # Get each item from the sublist (related->also bought) and add to one list if not empty
    all_ids = [val for meta in df.related.tolist() \
                        for val in meta[0] if meta[0] is not None]

    # Condense to a list of unique ids to eliminate any overlap
    all_unique_ids = list(set(all_ids))

    return all_unique_ids

def new_id_dictionary(df, column, suffix_val):
    """Take in column with unique indexes, return dictionary with new index values. This is done to
     remove the default ASIN and user ID from Amazon reviews and create better unique ids.
    Args:
        df: source dataframe
        column: name of column with ids to replace
        suffix_val: new suffix value for unique codes. Example: all new user_ids could end
        with '00000'
    Returns:
        new_id_dict: New Spark dataframe with column of new unique ids
    
    """
    unique_vals = list(set([old_id[0] for old_id in df.select(column).collect()]))
    new_ids = [(str(i) + suffix_val) for i in range(1,len(unique_vals)+1)]
    new_id_dict = {k:v for k,v in zip(unique_vals, new_ids)}
    return new_id_dict


def get_title_by_asin(asin):
    """Scraping function using Selenium for getting product names by Amazon ASIN.
    Args:
      asin: Amazon ASIN unique product ID 
    Returns:
      title: title of product on Amazon with that ASIN 
    """
    # Create search result page from ASIN
    link  = "https://www.amazon.com/s?k=" + asin + "&ref=nb_sb_noss"

    # Instatiate browser
    browser = Chrome()

    browser.get(link)

    # Find title by unique XPath to first result (most common format)
    try:
        title = browser.find_element_by_xpath('//*[@id="search"]/div[1]/div[2]/div/span[3]/div[1]\
                                            /div/div/div/div/div[2]/div[2]/div/div[1]/div/div/div\
                                            [1]/h2/a/span').text
    
    except:
        title = 'None found'
    browser.close()
    return title


def get_missing_titles(asin_list):
    """Take in a list of ASINs and return a list of dictionaries with their correct title
        Args:
      asin_list: list of ASINs to search for on Amazon
    Returns:
      asin_title_list: a new list of dictionaries with the asin and title for each"""
    asin_title_list = []

    # Run each ASIN through the get_title_by_asin function and return dictionary
    for asin in asin_list:
        title = get_title_by_asin(asin)
        new_temp_dict = {'asin': asin, 'title': title}
        asin_title_list.append(new_temp_dict)
        time.sleep(5)
    return asin_title_list


def get_user_reviews():
    """Take user input and create dataframe added for recommending
    Args:
        None
    Returns:
        pd.DataFrame(reviews): Pandas dataframe of users reviews from inputs
    """
    # Instantiate Spark & load reviews
    spark = (pyspark.sql.SparkSession.builder
    .master("local")
    .getOrCreate())
    
    all_reviews = spark.read.json('data/all_reviews_fixed_titles.json')

    # Make a dataframe of just movies
    query = """
        SELECT 
            DISTINCT CAST(item_id as string) as item_id
        ,   title
        ,   count
        FROM 
            table
        WHERE 
            item_id LIKE '%44'"""

    all_reviews.createOrReplaceTempView('table')

    get_movies = spark.sql(query).toPandas()

    # Sort dataframe by review count, take a random sample from the top 500 reviewed
    get_movies.sort_values('count', ascending=False, inplace=True)
    movie_rand_sample = get_movies[:500].sample(n=100)

    reviews = []

    # Give a user input and movie title and take in score
    for index, movie in movie_rand_sample.iterrows():
        print(movie['title'])
        rating = input("How would you rate this movie? (0-5, OR type 'skip'): ")
        # If user has not seen, can enter skip instead
        if rating == 'skip':
            clear_output()
            continue
        # Creating dictionary of review and adding to reviews
        else:
            movie_rating = {'user_id': 101, 'overall': int(rating),
                            'item_id': int(movie['item_id']), 'count':movie['count'],
                            'title': movie['title']}
            reviews.append(movie_rating)
            clear_output()
            if len(reviews) >=10:
                return pd.DataFrame(reviews)
            else:
                continue


def get_recommendations(new_user_df, new_user=101):
    """Get recommendations for new user!
    Args:
        new_user: id given for new user, defaults to standard 101
        new_user_df: Pandas dataframe with user reviews, as generated from get_user_reviews function
    Returns:
        Prints top three recommended comics for new user
    """
    # Instantiate Spark session & load reviews
    spark = (pyspark.sql.SparkSession.builder
    .master("local")
    .getOrCreate())
    
    new_user_spark = spark.createDataFrame(new_user_df)

    all_reviews = spark.read.json('data/all_reviews_fixed_titles.json')
    
    # Combine user reviews with others and prep for modeling
    ratings_all = all_reviews.select(['count', 'item_id','overall','title','user_id'])\
                             .union(new_user_spark)

    als_ready = ratings_all.select([F.col("user_id").cast(IntegerType()),
                                    F.col("item_id").cast(IntegerType()),
                                    F.col("overall")])
    
    # Create ALS model 
    als = ALS(rank=50, regParam=0.01, maxIter=20,
      userCol='user_id', itemCol='item_id', 
      ratingCol='overall', nonnegative=True)
    
    als_model = als.fit(als_ready)
    
    # Get recommendations for user and only return those that are comics & top three
    user_recommend = als_model.recommendForAllUsers(30)
    recs_for_user = user_recommend.where(user_recommend.user_id == new_user).take(1)
    all_comics = [reco[0] for reco in recs_for_user[0]['recommendations']\
                  if str(reco[0]).endswith('22') ]
    comic_titles = list(set(all_reviews.filter(F.col('item_id')\
                                       .isin(all_comics))\
                                       .select('title').collect()))
    for comic in comic_titles[:4]:
        print(comic[0])


def get_item_image(df, directory):
    """Get images from  dataframe. This is meant specifically to work with metadata dataframe 
    Args:
        df: subset dataframe to extract from
    Returns:
        saves images to 'images/' folder
    """
    for index, item in df.iterrows():
        url = item['imUrl']
        # Concats items ASIN and original file extension to create name
        filename = directory + str(item['item_id']) + item['imUrl'][-4:]
        try:
            urllib.request.urlretrieve(url, filename)
        except:
            continue

def row_to_html_card(row):
    """Takes a row from a dataframe and returns the card html needed for the web app
    Args:
    row: individual row from Pandas dataframe with title, asin, and item_id
    Returns:
    card_html: html text for an html card"""
    title = row['title']
    filename = 'images/movies/' + str(row['asin']) + '.jpg'
    item_id = str(row['item_id'])
    card_html = f"""
    <div class="col-md-3">
        <div class="card bg-light">
            <img class="card-img-top poster-img" alt="{title}"
                src="{filename}">
            <div class="card-block">
                <h4 class="card-title text-center">
                    <b>{title}</b>
                </h4>
                <div class="container">
                    <div class="row">
                        <div class="mx-auto">
                            <input type="radio" id="star1" name="{item_id}"
                                value=1 /><label for="star1" title="HATE">1</label>
                            <input type="radio" id="star2" name="{item_id}"
                                value=2 /><label for="star2" title="Meh">2</label>
                            <input type="radio" id="star3" name="{item_id}"
                                value=3 /><label for="star3" title="Just okay">3</label>
                            <input type="radio" id="star4" name="{item_id}"
                                value=4 /><label for="star4" title="Good">4</label>
                            <input type="radio" id="star5" name="{item_id}"
                                value=5 /><label for="star5" title="Love it!">5</label>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    """
    return card_html
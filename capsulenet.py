"""
Keras implementation of CapsNet in Hinton's paper Dynamic Routing Between Capsules.
The current version maybe only works for TensorFlow backend. Actually it will be straightforward to re-write to TF code.
Adopting to other backends should be easy, but I have not tested this. 

Usage:
       python CapsNet.py
       python CapsNet.py --epochs 50
       python CapsNet.py --epochs 50 --num_routing 3
       ... ...
"""
import numpy as np
from keras import layers, models, optimizers
from keras import backend as K
from keras.utils import to_categorical
import matplotlib.pyplot as plt
from utils import combine_images
from PIL import Image
from capsulelayers import CapsuleLayer, PrimaryCap, Length, Mask
# import numpy as np
# from tensorflow.keras import layers, models, optimizers
# # from tensorflow.keras import backend as K
# from tensorflow.keras.utils import to_categorical
# from capsulelayers import CapsuleLayer, PrimaryCap, Length, Mask
# import tensorflow.keras.backend as K

K.set_image_data_format('channels_last')


def CapsNet(input_shape, n_class, routings):
    """
    A Capsule Network on MNIST.
    :param input_shape: data shape, 3d, [width, height, channels]
    :param n_class: number of classes
    :param routings: number of routing iterations
    :return: Two Keras Models, the first one used for training, and the second one for evaluation.
            `eval_model` can also be used for training.
    """
    x = layers.Input(shape=input_shape)

    # Layer 1: Just a conventional Conv2D layer
    conv1 = layers.Conv2D(filters=256, kernel_size=9, strides=1, padding='valid', activation='relu', name='conv1')(x)

    # Layer 2: Conv2D layer with `squash` activation, then reshape to [None, num_capsule, dim_capsule]
    primarycaps = PrimaryCap(conv1, dim_capsule=8, n_channels=32, kernel_size=9, strides=2, padding='valid')

    # Layer 3: Capsule layer. Routing algorithm works here.
    digitcaps = CapsuleLayer(num_capsule=n_class, dim_capsule=16, routings=routings,
                             name='digitcaps')(primarycaps)

    # Layer 4: This is an auxiliary layer to replace each capsule with its length. Just to match the true label's shape.
    # If using tensorflow, this will not be necessary. :)
    out_caps = Length(name='capsnet')(digitcaps)

    # Decoder network.
    y = layers.Input(shape=(n_class,))
    masked_by_y = Mask()([digitcaps, y])  # The true label is used to mask the output of capsule layer. For training
    masked = Mask()(digitcaps)  # Mask using the capsule with maximal length. For prediction

    # Shared Decoder model in training and prediction
    decoder = models.Sequential(name='decoder')
    decoder.add(layers.Dense(512, activation='relu', input_dim=16*n_class))
    decoder.add(layers.Dense(1024, activation='relu'))
    decoder.add(layers.Dense(np.prod(input_shape), activation='sigmoid'))
    decoder.add(layers.Reshape(target_shape=input_shape, name='out_recon'))

    # Models for training and evaluation (prediction)
    train_model = models.Model([x, y], [out_caps, decoder(masked_by_y)])
    eval_model = models.Model(x, [out_caps, decoder(masked)])

    # manipulate model
    noise = layers.Input(shape=(n_class, 16))
    noised_digitcaps = layers.Add()([digitcaps, noise])
    masked_noised_y = Mask()([noised_digitcaps, y])
    manipulate_model = models.Model([x, y, noise], decoder(masked_noised_y))
    return train_model, eval_model, manipulate_model


def margin_loss(y_true, y_pred):
    """
    Margin loss for Eq.(4). When y_true[i, :] contains not just one `1`, this loss should work too. Not test it.
    :param y_true: [None, n_classes]
    :param y_pred: [None, num_capsule]
    :return: a scalar loss value.
    """
    L = y_true * K.square(K.maximum(0., 0.9 - y_pred)) + \
        0.5 * (1 - y_true) * K.square(K.maximum(0., y_pred - 0.1))

    return K.mean(K.sum(L, 1))


def train(model, data, args):
    """
    Training a CapsuleNet
    :param model: the CapsuleNet model
    :param data: a tuple containing training and testing data, like `((x_train, y_train), (x_test, y_test))`
    :param args: arguments
    :return: The trained model
    """
    # unpacking the data
    (x_train, y_train), (x_test, y_test) = data

    # callbacks
    log = callbacks.CSVLogger(args.save_dir + '/log.csv')
    tb = callbacks.TensorBoard(log_dir=args.save_dir + '/tensorboard-logs',
                               batch_size=args.batch_size, histogram_freq=int(args.debug))
    checkpoint = callbacks.ModelCheckpoint(args.save_dir + '/weights-{epoch:02d}.h5', monitor='val_capsnet_acc',
                                           save_best_only=True, save_weights_only=True, verbose=1)
    lr_decay = callbacks.LearningRateScheduler(schedule=lambda epoch: args.lr * (args.lr_decay ** epoch))

    # compile the model
    model.compile(optimizer=optimizers.Adam(lr=args.lr),
                  loss=[margin_loss, 'mse'],
                  loss_weights=[1., args.lam_recon],
                  metrics={'capsnet': 'accuracy'})

    """
    # Training without data augmentation:
    model.fit([x_train, y_train], [y_train, x_train], batch_size=args.batch_size, epochs=args.epochs,
              validation_data=[[x_test, y_test], [y_test, x_test]], callbacks=[log, tb, checkpoint, lr_decay])
    """

    # Begin: Training with data augmentation ---------------------------------------------------------------------#
    def train_generator(x, y, batch_size, shift_fraction=0.):
        train_datagen = ImageDataGenerator(width_shift_range=shift_fraction,
                                           height_shift_range=shift_fraction)  # shift up to 2 pixel for MNIST
        generator = train_datagen.flow(x, y, batch_size=batch_size)
        while 1:
            x_batch, y_batch = generator.next()
            yield ([x_batch, y_batch], [y_batch, x_batch])

    # Training with data augmentation. If shift_fraction=0., also no augmentation.
    model.fit_generator(generator=train_generator(x_train, y_train, args.batch_size, args.shift_fraction),
                        steps_per_epoch=int(y_train.shape[0] / args.batch_size),
                        epochs=args.epochs,
                        validation_data=[[x_test, y_test], [y_test, x_test]],
                        callbacks=[log, tb, checkpoint, lr_decay])
    # End: Training with data augmentation -----------------------------------------------------------------------#

    model.save_weights(args.save_dir + '/trained_model.h5')
    print('Trained model saved to \'%s/trained_model.h5\'' % args.save_dir)

    from utils import plot_log
    plot_log(args.save_dir + '/log.csv', show=True)

    return model


def test(model, data, args):
    x_test, y_test = data
    y_pred, x_recon = model.predict(x_test, batch_size=100)
    print('-'*30 + 'Begin: test' + '-'*30)
    print('Test acc:', np.sum(np.argmax(y_pred, 1) == np.argmax(y_test, 1))/y_test.shape[0])

    img = combine_images(np.concatenate([x_test[:50],x_recon[:50]]))
    image = img * 255
    Image.fromarray(image.astype(np.uint8)).save(args.save_dir + "/real_and_recon.png")
    print()
    print('Reconstructed images are saved to %s/real_and_recon.png' % args.save_dir)
    print('-' * 30 + 'End: test' + '-' * 30)
    plt.imshow(plt.imread(args.save_dir + "/real_and_recon.png"))
    plt.show()


def manipulate_latent(model, data, args):
    print('-'*30 + 'Begin: manipulate' + '-'*30)
    x_test, y_test = data
    index = np.argmax(y_test, 1) == args.digit
    number = np.random.randint(low=0, high=sum(index) - 1)
    x, y = x_test[index][number], y_test[index][number]
    x, y = np.expand_dims(x, 0), np.expand_dims(y, 0)
    noise = np.zeros([1, 10, 16])
    x_recons = []
    for dim in range(16):
        for r in [-0.25, -0.2, -0.15, -0.1, -0.05, 0, 0.05, 0.1, 0.15, 0.2, 0.25]:
            tmp = np.copy(noise)
            tmp[:,:,dim] = r
            x_recon = model.predict([x, y, tmp])
            x_recons.append(x_recon)

    x_recons = np.concatenate(x_recons)

    img = combine_images(x_recons, height=16)
    image = img*255
    Image.fromarray(image.astype(np.uint8)).save(args.save_dir + '/manipulate-%d.png' % args.digit)
    print('manipulated result saved to %s/manipulate-%d.png' % (args.save_dir, args.digit))
    print('-' * 30 + 'End: manipulate' + '-' * 30)


##############################################################################################
import os,cv2
import numpy as np
import matplotlib.pyplot as plt

from sklearn.utils import shuffle
from sklearn.model_selection import train_test_split

from keras import backend as K
K.set_image_data_format('channels_last')


from keras import utils
from keras.models import Sequential
from keras.layers import Dense, Dropout, Activation, Flatten
from keras.layers import Convolution2D, MaxPooling2D
from keras.optimizers import SGD,RMSprop,Adam

#%%

PATH = os.getcwd()
# Define data path
data_path = PATH + '/data_train'
data_dir_list = os.listdir(data_path)

img_rows=28
img_cols=28
num_channel=1
num_epoch=20

# Define the number of classes
num_classes = 9

img_data_list=[]

for dataset in data_dir_list:
	img_list=os.listdir(data_path+'/'+ dataset)
	print ('Loaded the images of dataset-'+'{}\n'.format(dataset))
	for img in img_list:
		input_img=cv2.imread(data_path + '/'+ dataset + '/'+ img )
		input_img=cv2.cvtColor(input_img, cv2.COLOR_BGR2GRAY)
		input_img_resize=cv2.resize(input_img,(28,28))
		img_data_list.append(input_img_resize)

img_data = np.array(img_data_list)
img_data = img_data.astype('float32')
img_data /= 255
print (img_data.shape)

if num_channel==1:
 	if K.image_data_format =='channels_last':
         img_data= np.expand_dims(img_data, axis=1) 
         print (img_data.shape)
 	else:
         img_data= np.expand_dims(img_data, axis=3) 
         print (img_data.shape)
		
else:
 	if K.image_data_format =='channels_last':
         img_data=np.rollaxis(img_data,3,1)
         print (img_data.shape)
		
#%%
USE_SKLEARN_PREPROCESSING=False

if USE_SKLEARN_PREPROCESSING:
 	# using sklearn for preprocessing
 	from sklearn import preprocessing
 	
 	def image_to_feature_vector(image, size=(28, 28)):
		# resize the image to a fixed size, then flatten the image into
		# a list of raw pixel intensities
		 return cv2.resize(image, size).flatten()
 	
 	img_data_list=[]
 	for dataset in data_dir_list:
		 img_list=os.listdir(data_path+'/'+ dataset)
		 print ('Loaded the images of dataset-'+'{}\n'.format(dataset))
		 for img in img_list:
 			input_img=cv2.imread(data_path + '/'+ dataset + '/'+ img )
 			input_img=cv2.cvtColor(input_img, cv2.COLOR_BGR2GRAY)
 			input_img_flatten=image_to_feature_vector(input_img,(128,128))
 			img_data_list.append(input_img_flatten)
 	
 	img_data = np.array(img_data_list)
 	img_data = img_data.astype('float32')
 	print (img_data.shape)
 	img_data_scaled = preprocessing.scale(img_data)
 	print (img_data_scaled.shape)
 	
 	print (np.mean(img_data_scaled))
 	print (np.std(img_data_scaled))
 	
 	print (img_data_scaled.mean(axis=0))
 	print (img_data_scaled.std(axis=0))
 	
 	if K.image_data_format =='channels_last':
	 	img_data_scaled=img_data_scaled.reshape(img_data.shape[0],num_channel,img_rows,img_cols)
	 	print (img_data_scaled.shape)
		
 	else:
	 	img_data_scaled=img_data_scaled.reshape(img_data.shape[0],img_rows,img_cols,num_channel)
	 	print (img_data_scaled.shape)
 	
 	
 	if K.image_data_format =='channels_last':
	 	img_data_scaled=img_data_scaled.reshape(img_data.shape[0],num_channel,img_rows,img_cols)
	 	print (img_data_scaled.shape)
		
 	else:
	 	img_data_scaled=img_data_scaled.reshape(img_data.shape[0],img_rows,img_cols,num_channel)
	 	print (img_data_scaled.shape)

if USE_SKLEARN_PREPROCESSING:
 	img_data=img_data_scaled
#%%
# Assigning Labels

# Define the number of classes
num_classes = 9

num_of_samples = img_data.shape[0]
labels = np.ones((num_of_samples,),dtype='int64')

labels[0:677]=0
labels[678:1345]=1
labels[1346:2011]=2
labels[2011:2681]=3
labels[2681:3369]=4
labels[3369:4043]=5
labels[4043:4721]=6
labels[4721:5403]=7
labels[5403:6086]=8

names = ['1','2','3','4','5','6','7','8','9']
	  
# convert class labels to on-hot encoding
Y = utils.to_categorical(labels, num_classes)
x_train= img_data
y_train=Y
###############################################################################################
##############################################################################################
PATH = os.getcwd()
# Define data path
data_path = PATH + '/data_test'
data_dir_list = os.listdir(data_path)

img_rows=28
img_cols=28
num_channel=1
num_epoch=20

# Define the number of classes
num_classes = 9

img_data_list=[]

for dataset in data_dir_list:
	img_list=os.listdir(data_path+'/'+ dataset)
	print ('Loaded the images of dataset-'+'{}\n'.format(dataset))
	for img in img_list:
		input_img=cv2.imread(data_path + '/'+ dataset + '/'+ img )
		input_img=cv2.cvtColor(input_img, cv2.COLOR_BGR2GRAY)
		input_img_resize=cv2.resize(input_img,(28,28))
		img_data_list.append(input_img_resize)

img_data = np.array(img_data_list)
img_data = img_data.astype('float32')
img_data /= 255
print (img_data.shape)

if num_channel==1:
 	if K.image_data_format =='channels_last':
	 	img_data= np.expand_dims(img_data, axis=1) 
	 	print (img_data.shape)
 	else:
	 	img_data= np.expand_dims(img_data, axis=3) 
	 	print (img_data.shape)
		
else:
 	if K.image_data_format =='channels_last':
	 	img_data=np.rollaxis(img_data,3,1)
	 	print (img_data.shape)
		
#%%
USE_SKLEARN_PREPROCESSING=False

if USE_SKLEARN_PREPROCESSING:
 	# using sklearn for preprocessing
 	from sklearn import preprocessing
 	
 	def image_to_feature_vector(image, size=(28, 28)):
		# resize the image to a fixed size, then flatten the image into
		# a list of raw pixel intensities
		 return cv2.resize(image, size).flatten()
 	
 	img_data_list=[]
 	for dataset in data_dir_list:
		 img_list=os.listdir(data_path+'/'+ dataset)
		 print ('Loaded the images of dataset-'+'{}\n'.format(dataset))
		 for img in img_list:
 			input_img=cv2.imread(data_path + '/'+ dataset + '/'+ img )
 			input_img=cv2.cvtColor(input_img, cv2.COLOR_BGR2GRAY)
 			input_img_flatten=image_to_feature_vector(input_img,(128,128))
 			img_data_list.append(input_img_flatten)
 	
 	img_data = np.array(img_data_list)
 	img_data = img_data.astype('float32')
 	print (img_data.shape)
 	img_data_scaled = preprocessing.scale(img_data)
 	print (img_data_scaled.shape)
 	
 	print (np.mean(img_data_scaled))
 	print (np.std(img_data_scaled))
 	
 	print (img_data_scaled.mean(axis=0))
 	print (img_data_scaled.std(axis=0))
 	
 	if K.image_data_format =='channels_last':
		 img_data_scaled=img_data_scaled.reshape(img_data.shape[0],num_channel,img_rows,img_cols)
		 print (img_data_scaled.shape)
		
 	else:
		 img_data_scaled=img_data_scaled.reshape(img_data.shape[0],img_rows,img_cols,num_channel)
		 print (img_data_scaled.shape)
 	
 	
 	if K.image_data_format =='channels_last':
		 img_data_scaled=img_data_scaled.reshape(img_data.shape[0],num_channel,img_rows,img_cols)
		 print (img_data_scaled.shape)
		
 	else:
	 	img_data_scaled=img_data_scaled.reshape(img_data.shape[0],img_rows,img_cols,num_channel)
	 	print (img_data_scaled.shape)

if USE_SKLEARN_PREPROCESSING:
 	img_data=img_data_scaled
#%%
# Assigning Labels

# Define the number of classes
num_classes = 9

num_of_samples = img_data.shape[0]
labels = np.ones((num_of_samples,),dtype='int64')

labels[0:146]=0
labels[146:290]=1
labels[290:431]=2
labels[431:575]=3
labels[575:723]=4
labels[723:868]=5
labels[868:1014]=6
labels[1014:1156]=7
labels[1156:1303]=8

names = ['1','2','3','4','5','6','7','8','9']
	  
# convert class labels to on-hot encoding
Y = utils.to_categorical(labels, num_classes)
x_test= img_data
y_test=Y
##############################################################################################
if __name__ == "__main__":
    import os
    import argparse
    from keras.preprocessing.image import ImageDataGenerator
    from keras import callbacks

    # setting the hyper parameters
    parser = argparse.ArgumentParser(description="Capsule Network on MNIST.")
    parser.add_argument('--epochs', default=50, type=int)
    parser.add_argument('--batch_size', default=100, type=int)
    parser.add_argument('--lr', default=0.001, type=float,
                        help="Initial learning rate")
    parser.add_argument('--lr_decay', default=0.9, type=float,
                        help="The value multiplied by lr at each epoch. Set a larger value for larger epochs")
    parser.add_argument('--lam_recon', default=0.392, type=float,
                        help="The coefficient for the loss of decoder")
    parser.add_argument('-r', '--routings', default=3, type=int,
                        help="Number of iterations used in routing algorithm. should > 0")
    parser.add_argument('--shift_fraction', default=0.1, type=float,
                        help="Fraction of pixels to shift at most in each direction.")
    parser.add_argument('--debug', action='store_true',
                        help="Save weights by TensorBoard")
    parser.add_argument('--save_dir', default='./result')
    parser.add_argument('-t', '--testing', action='store_true',
                        help="Test the trained model on testing dataset")
    parser.add_argument('--digit', default=5, type=int,
                        help="Digit to manipulate")
    parser.add_argument('-w', '--weights', default=None,
                        help="The path of the saved weights. Should be specified when testing")
    args = parser.parse_args()
    print(args)

    if not os.path.exists(args.save_dir):
        os.makedirs(args.save_dir)

    # load data
    #(x_train, y_train), (x_test, y_test) = load_mnist()
   
    # define model
    model, eval_model, manipulate_model = CapsNet(input_shape=x_train.shape[1:],
                                                  n_class=len(np.unique(np.argmax(y_train, 1))),
                                                  routings=args.routings)
    model.summary()

    # train or test
    if args.weights is not None:  # init the model weights with provided one
        model.load_weights(args.weights)
    if not args.testing:
        train(model=model, data=((x_train, y_train), (x_test, y_test)), args=args)
    else:  # as long as weights are given, will run testing
        if args.weights is None:
            print('No weights are provided. Will test using random initialized weights.')
        manipulate_latent(manipulate_model, (x_test, y_test), args)
        test(model=eval_model, data=(x_test, y_test), args=args)
